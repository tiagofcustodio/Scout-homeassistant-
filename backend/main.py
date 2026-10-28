import asyncio
import json
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.ai_brain import MultiProviderAIBrain
from backend.domestic_features import DomesticFeaturesManager, SNAPSHOTS_DIR
from backend.ha_bridge import HomeAssistantBridge
from backend.identity_memory import BiometricIdentityMemory
from backend.patrol_mapper import PatrolAndMapper
from backend.scanner import scan_network_for_scout
from backend.scout_client import ScoutClient
from backend.xbox_controller import XboxControllerManager

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
LOG_FILE = BASE_DIR / "scout_messages.log"

scout = ScoutClient(
    scout_ip=os.getenv("SCOUT_IP", "192.168.1.115"),
    rosbridge_port=int(os.getenv("ROSBRIDGE_PORT", "9090")),
    mock_mode=os.getenv("MOCK_MODE", "false").lower() == "true",
)
ai_brain = MultiProviderAIBrain(scout=scout)
identity_memory = BiometricIdentityMemory()
ha_bridge = HomeAssistantBridge()
patrol_mapper = PatrolAndMapper(scout=scout)
xbox_controller = XboxControllerManager(scout=scout, patrol_mapper=patrol_mapper)
_last_face_check_ts: float = 0.0
_awaiting_name_until: float = 0.0
_pending_unknown_voice_pcm: Optional[bytes] = None

app = FastAPI(title="Moorebot Scout AI & Control Hub", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class MoveCommand(BaseModel):
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    duration_s: float = 0.0


class ConfigUpdate(BaseModel):
    scout_ip: str
    mock_mode: bool


class AIProviderUpdate(BaseModel):
    provider: str
    model: Optional[str] = None


class ChatRequest(BaseModel):
    prompt: str
    include_vision: bool = True
    speak_on_scout: bool = True


class SpeakRequest(BaseModel):
    text: str


class AudioSettingsUpdate(BaseModel):
    speaker_volume_pct: Optional[int] = None
    mic_vad_listening: Optional[bool] = None
    mic_vad_threshold_rms: Optional[float] = None
    mic_sensitivity_pct: Optional[int] = None
    mic_require_wake_word: Optional[bool] = None
    wake_now: Optional[bool] = None
    voice: Optional[str] = None
    voice_rate: Optional[str] = None
    voice_pitch: Optional[str] = None


class EnrollPersonRequest(BaseModel):
    name: str
    include_face: bool = True
    include_last_voice: bool = True


class HAConfigRequest(BaseModel):
    ha_url: str
    ha_token: str = ""


class HAServiceCallRequest(BaseModel):
    domain: str
    service: str
    entity_id: str


class PersonalityRequest(BaseModel):
    personality_id: str
    speak_greeting: bool = True


class StuntRequest(BaseModel):
    stunt: str


class SnapshotRequest(BaseModel):
    label: str = "Foto Manual"
    speak_shutter: bool = True


class SentryRequest(BaseModel):
    enabled: Optional[bool] = None
    alarm_sound: Optional[bool] = None


class TimerRequest(BaseModel):
    seconds: int
    label: str = "Alarme do Scout"


message_logs: List[Dict] = []
_log_counter = 0


def record_message_log(source: str, sender: str, text: str, status: str = "processed") -> Dict:
    """Regista todas as mensagens (Voz do Scout, Web Chat, Respostas da IA e Frases Ignoradas) em memoria e em ficheiro."""
    global _log_counter
    _log_counter += 1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "id": _log_counter,
        "timestamp": now_str,
        "source": source,      # "mic_scout" | "web_chat" | "ai_reply" | "mic_ignored"
        "sender": sender,
        "text": text,
        "status": status,
    }
    message_logs.append(entry)
    if len(message_logs) > 200:
        message_logs.pop(0)

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now_str}] [{source.upper()}] {sender}: {text}\n")
    except Exception:
        pass
    return entry


domestic = DomesticFeaturesManager(
    scout=scout,
    identity_memory=identity_memory,
    log_callback=record_message_log,
)


async def _enroll_with_countdown(person_name: str, current_pcm: Optional[bytes]) -> Dict:
    """
    Faz a contagem crescente 1, 2, 3 capturando 3 frames consecutivos da câmara (/CoreNode/jpg)
    enquanto a pessoa olha diretamente para a lente, além de memorizar as amostras de voz!
    """
    global _pending_unknown_voice_pcm

    # Se havia uma primeira amostra de voz guardada quando a pessoa falou pela 1.ª vez, regista-a já
    if _pending_unknown_voice_pcm:
        await asyncio.to_thread(
            identity_memory.enroll_person,
            person_name,
            None,
            _pending_unknown_voice_pcm,
        )
        _pending_unknown_voice_pcm = None

    last_res: Dict = {}
    pcm_to_save = current_pcm or scout.last_voice_pcm
    for i in range(3):
        cam_bytes = scout.get_camera_jpeg_bytes()
        last_res = await asyncio.to_thread(
            identity_memory.enroll_person,
            person_name,
            cam_bytes,
            pcm_to_save if i == 0 else None,
        )
        if i < 2:
            await asyncio.sleep(0.45)

    return last_res


async def _process_interaction_with_biometrics(
    raw_text: str,
    cleaned_prompt: str,
    norm_pcm: Optional[bytes],
    source_tag: str,
    source_label: str,
    speak_reply: bool = True,
) -> Dict:
    """
    Fluxo completo de Reconhecimento Biométrico (Cara + Voz):
    1. Se estava a aguardar o nome de uma pessoa desconhecida (ou se a pessoa disse 'Eu sou o X'),
       faz a contagem 'Um, dois, três, rosto e voz memorizados!' e grava 3 fotos do rosto + amostras de voz.
    2. Se não reconhecer a cara nem a voz da pessoa, responde:
       'Olá, desculpa mas não reconheço a tua cara nem a tua voz. Poderias dizer-me o teu nome e olhar diretamente para a câmara?'
       e fica a aguardar o nome.
    3. Se reconhecer a pessoa pela cara e/ou voz, responde normalmente pelo cérebro IA tratando-a pelo nome!
    """
    global _awaiting_name_until, _pending_unknown_voice_pcm
    import time as _time

    now = _time.time()
    cam_bytes = scout.get_camera_jpeg_bytes()
    if norm_pcm:
        identity_memory.cache_last_voice_pcm(norm_pcm)

    # Caso 1: Estávamos à espera que a pessoa dissesse o nome (ou disse explicitamente "Eu sou o [Nome]")
    is_waiting_name = now < _awaiting_name_until
    candidate_name = (
        identity_memory.extract_name_when_prompted(cleaned_prompt)
        if is_waiting_name
        else identity_memory.detect_enrollment_intent(raw_text)
    )

    if candidate_name:
        _awaiting_name_until = 0.0
        scout._awake_until = _time.time() + 45.0
        record_message_log(source_tag, f"👤 {candidate_name} ({source_label})", raw_text, "enrolled")

        countdown_reply = (
            f"Um... dois... três! Rosto e voz memorizados! "
            f"Prazer em conhecer-te, {candidate_name}!"
        )
        if speak_reply:
            # Inicia a fala "Um... dois... três!" na coluna do Scout enquanto capta as 3 fotos na câmara!
            asyncio.create_task(scout.speak_on_robot(countdown_reply))

        enroll_res = await _enroll_with_countdown(candidate_name, norm_pcm)
        p_info = enroll_res.get("person", {})
        record_message_log(
            "ai_reply",
            f"🤖 Scout (Memória Biométrica · {p_info.get('face_samples', 3)} caras / {p_info.get('voice_samples', 1)} vozes)",
            countdown_reply,
            "replied",
        )
        return {
            "provider": "memória biométrica",
            "reply": countdown_reply,
            "actions": [],
        }

    # Caso 2: Verificar se reconhecemos a Cara e/ou a Voz
    live = await asyncio.to_thread(identity_memory.identify, cam_bytes, norm_pcm or scout.last_voice_pcm)
    person = live.get("active_person")
    conf = live.get("active_confidence_pct", 0)
    v_match = live.get("voice_match")
    v_conf = live.get("voice_confidence_pct", 0)
    f_match = live.get("face_match")
    f_conf = live.get("face_confidence_pct", 0)

    if not person or conf < 55:
        # Não reconheceu nem a cara nem a voz! Pede o nome e ativa espera de 60s
        _awaiting_name_until = _time.time() + 60.0
        scout._awake_until = _time.time() + 45.0
        if norm_pcm:
            _pending_unknown_voice_pcm = norm_pcm

        record_message_log(source_tag, f"❓ Desconhecido ({source_label})", raw_text, "unknown_person")
        unknown_reply = (
            "Olá, desculpa mas não reconheço a tua cara nem a tua voz. "
            "Poderias dizer-me o teu nome e olhar diretamente para a câmara?"
        )
        record_message_log("ai_reply", "🤖 Scout (Reconhecimento Biométrico)", unknown_reply, "replied")
        if speak_reply:
            asyncio.create_task(scout.speak_on_robot(unknown_reply))
        return {
            "provider": "reconhecimento biométrico",
            "reply": unknown_reply,
            "actions": [],
        }

    # Caso 3: Pessoa reconhecida! Registar landmark no mapa 2D e verificar comandos de Patrulha ou Casa Inteligente
    patrol_mapper.add_landmark(f"👤 {person}")
    record_message_log(source_tag, f"🎤 {person} ({conf}% · {source_label})", raw_text, "accepted")

    p_low = cleaned_prompt.lower()
    if any(w in p_low for w in ["parar patrulha", "para a patrulha", "termina a patrulha", "parar mapeamento"]):
        await patrol_mapper.stop_patrol()
        patrol_reply = f"Entendido {person}, parei a patrulha e guardei o mapa da casa!"
        record_message_log("ai_reply", "🗺️ Scout (Patrulha & Mapa)", patrol_reply, "replied")
        if speak_reply:
            asyncio.create_task(scout.speak_on_robot(patrol_reply))
        return {"provider": "patrol_mapper", "reply": patrol_reply, "actions": ["stop_patrol"]}

    if any(w in p_low for w in ["patrulhar", "inicia patrulha", "começa a patrulhar", "comeca a patrulhar", "mapear", "mapeia", "explorar a casa", "explora a sala"]):
        await patrol_mapper.start_patrol()
        patrol_reply = f"Às tuas ordens {person}! Vou começar a patrulhar e a mapear a área da casa."
        record_message_log("ai_reply", "🗺️ Scout (Patrulha & Mapa)", patrol_reply, "replied")
        if speak_reply:
            asyncio.create_task(scout.speak_on_robot(patrol_reply))
        return {"provider": "patrol_mapper", "reply": patrol_reply, "actions": ["start_patrol"]}

    # Comandos por voz para Coreografias, Fotos e Modo Sentinela
    if any(w in p_low for w in ["dança", "danca", "dançar", "dancar", "baila"]):
        await domestic.perform_stunt("dance")
        return {"provider": "domestic_stunt", "reply": "Olha só o meu ritmo nas rodas Mecanum!", "actions": ["stunt:dance"]}

    if any(w in p_low for w in ["pirueta", "volta completa", "360", "gira tudo"]):
        await domestic.perform_stunt("spin360")
        return {"provider": "domestic_stunt", "reply": "Uau, lá vou eu numa volta completa!", "actions": ["stunt:spin360"]}

    if any(w in p_low for w in ["tira uma foto", "tirar foto", "tira foto", "fotografia"]):
        snap = domestic.take_snapshot(label=f"Pedida por {person}", speak_shutter=speak_reply)
        return {"provider": "photo_booth", "reply": f"Sorri, {person}! Fotografia capturada!", "actions": [snap]}

    if any(w in p_low for w in ["ativa modo sentinela", "ligar sentinela", "vigia a sala", "vigiar a casa"]):
        domestic.toggle_sentry(enabled=True)
        return {"provider": "sentry_guard", "reply": "Modo sentinela ativado. Estou a vigiar a divisão.", "actions": ["sentry:on"]}

    if any(w in p_low for w in ["desativa modo sentinela", "desligar sentinela", "parar sentinela"]):
        domestic.toggle_sentry(enabled=False)
        return {"provider": "sentry_guard", "reply": "Modo sentinela desativado.", "actions": ["sentry:off"]}

    ha_cmd_res = await ha_bridge.try_execute_voice_home_command(cleaned_prompt)
    if ha_cmd_res and ha_cmd_res.get("matched"):
        ha_reply = f"{ha_cmd_res.get('reply', '')}"
        record_message_log("ai_reply", "🏠 Scout (Home Assistant)", ha_reply, "replied")
        if speak_reply and ha_reply:
            asyncio.create_task(scout.speak_on_robot(ha_reply))
        return {
            "provider": "home_assistant",
            "reply": ha_reply,
            "actions": [ha_cmd_res],
        }

    pers = domestic.get_personality()
    speaker_ctx = (
        f"[{pers['prompt']}]\n"
        f"[PESSOA IDENTIFICADA: Quem está a falar contigo é {person} "
        f"(Confiança combinada: {conf}% | Rosto: {f_match or 'N/A'} {f_conf}% | Voz: {v_match or 'N/A'} {v_conf}%). "
        f"Trata {person} pelo nome de forma natural!]"
    )
    result = await ai_brain.ask(prompt=cleaned_prompt, include_vision=True, speaker_context=speaker_ctx)
    reply = result.get("reply", "")
    provider_label = result.get("provider", "ollama")
    record_message_log("ai_reply", f"🤖 Scout ({provider_label})", reply, "replied")
    if speak_reply and reply:
        asyncio.create_task(scout.speak_on_robot(reply))
    return result


@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_running_loop()
    xbox_controller.set_event_loop(loop)

    async def on_scout_mic_speech(raw_transcript: str, cleaned_prompt: str, norm_pcm: Optional[bytes] = None):
        """Chamado automaticamente quando o utilizador fala para o microfone fisico do Moorebot Scout (com 'Scout' ou janela 30s)."""
        await _process_interaction_with_biometrics(
            raw_text=raw_transcript,
            cleaned_prompt=cleaned_prompt,
            norm_pcm=norm_pcm,
            source_tag="mic_scout",
            source_label="Microfone do Scout",
            speak_reply=True,
        )

    async def on_scout_mic_ignored(raw_transcript: str, accepted: bool):
        """Regista no log quando o microfone do Scout ouve alguem falar na sala sem dizer 'Scout'."""
        record_message_log(
            "mic_ignored",
            "🔇 Microfone Scout (Ignorado - sem 'Scout')",
            raw_transcript,
            "ignored",
        )

    scout.set_speech_callback(loop, on_scout_mic_speech, on_scout_mic_ignored)


@app.get("/api/status")
async def get_status():
    global _last_face_check_ts
    import time as _time
    now = _time.time()
    if now - _last_face_check_ts >= 1.8 and identity_memory.people:
        _last_face_check_ts = now
        try:
            identity_memory.identify(jpeg_bytes=scout.get_camera_jpeg_bytes(), pcm_16k_bytes=None)
        except Exception:
            pass
    return {
        "robot": scout.get_status(),
        "ai": ai_brain.get_config(),
        "identity": identity_memory.get_summary(),
        "ha": ha_bridge.get_status(),
        "map": patrol_mapper.get_map_state(),
        "xbox": xbox_controller.get_status(),
        "domestic": domestic.get_status(),
        "voice": {
            "active": scout.active_voice,
            "rate": scout.active_voice_rate,
            "pitch": scout.active_voice_pitch,
        },
        "logs": message_logs[-40:],
    }


@app.post("/api/domestic/personality")
async def api_set_personality(req: PersonalityRequest):
    pers = domestic.set_personality(req.personality_id, speak_greeting=req.speak_greeting)
    return {"personality": pers, "domestic": domestic.get_status()}


@app.post("/api/domestic/stunt")
async def api_perform_stunt(req: StuntRequest):
    res = await domestic.perform_stunt(req.stunt)
    return {"result": res, "domestic": domestic.get_status()}


@app.post("/api/domestic/snapshot")
async def api_take_snapshot(req: SnapshotRequest):
    snap = domestic.take_snapshot(label=req.label, speak_shutter=req.speak_shutter)
    return {"snapshot": snap, "domestic": domestic.get_status()}


@app.delete("/api/domestic/snapshot/{snap_id}")
async def api_delete_snapshot(snap_id: str):
    domestic.delete_snapshot(snap_id)
    return {"domestic": domestic.get_status()}


@app.post("/api/domestic/sentry")
async def api_toggle_sentry(req: SentryRequest):
    status = domestic.toggle_sentry(enabled=req.enabled, alarm_sound=req.alarm_sound)
    return {"domestic": status}


@app.post("/api/domestic/timer")
async def api_add_timer(req: TimerRequest):
    tmr = domestic.add_timer(seconds=req.seconds, label=req.label)
    return {"timer": tmr, "domestic": domestic.get_status()}


@app.delete("/api/domestic/timer/{timer_id}")
async def api_cancel_timer(timer_id: str):
    domestic.cancel_timer(timer_id)
    return {"domestic": domestic.get_status()}


@app.post("/api/patrol/start")
async def api_start_patrol():
    state = await patrol_mapper.start_patrol()
    record_message_log("system", "🗺️ Patrulha Autónoma", "Patrulha e mapeamento 2D iniciados.", "patrol")
    return {"map": state}


@app.post("/api/patrol/stop")
async def api_stop_patrol():
    state = await patrol_mapper.stop_patrol()
    record_message_log("system", "🗺️ Patrulha Autónoma", "Patrulha parada e mapa guardado.", "patrol")
    return {"map": state}


@app.post("/api/patrol/reset-map")
async def api_reset_map():
    state = patrol_mapper.reset_map()
    return {"map": state}


@app.post("/api/ha/config")
async def update_ha_config(req: HAConfigRequest):
    ha_bridge.save_config(req.ha_url, req.ha_token)
    await ha_bridge.sync_entities(force=True)
    return {"ha": ha_bridge.get_status()}


@app.post("/api/ha/call")
async def call_ha_device(req: HAServiceCallRequest):
    res = await ha_bridge.call_service(req.domain, req.service, req.entity_id)
    await ha_bridge.sync_entities(force=True)
    return {"result": res, "ha": ha_bridge.get_status()}


@app.post("/api/identity/enroll")
async def enroll_person_api(req: EnrollPersonRequest):
    cam_bytes = scout.get_camera_jpeg_bytes() if req.include_face else None
    pcm_bytes = scout.last_voice_pcm if req.include_last_voice else None
    res = await asyncio.to_thread(identity_memory.enroll_person, req.name, cam_bytes, pcm_bytes)
    if res.get("ok"):
        p = res["person"]
        record_message_log(
            "system",
            "👤 Memória Biométrica",
            f"Perfil memorizado: {p['name']} (Amostras de Cara: {p['face_samples']}, Amostras de Voz: {p['voice_samples']})",
            "enrolled",
        )
    return {"result": res, "identity": identity_memory.get_summary()}


@app.delete("/api/identity/{person_id}")
async def delete_person_api(person_id: str):
    identity_memory.delete_person(person_id)
    return {"identity": identity_memory.get_summary()}


@app.get("/api/logs")
async def get_all_logs():
    return {
        "log_file": str(LOG_FILE),
        "entries": message_logs,
    }


@app.post("/api/audio/settings")
async def update_audio_settings(req: AudioSettingsUpdate):
    if req.speaker_volume_pct is not None:
        scout.set_speaker_volume(req.speaker_volume_pct)
    if req.voice:
        scout.active_voice = req.voice
    if req.voice_rate:
        scout.active_voice_rate = req.voice_rate
    if req.voice_pitch:
        scout.active_voice_pitch = req.voice_pitch
    scout.set_mic_vad(
        req.mic_vad_listening if req.mic_vad_listening is not None else scout.telemetry.mic_vad_listening,
        threshold_rms=req.mic_vad_threshold_rms,
        sensitivity_pct=req.mic_sensitivity_pct,
        require_wake_word=req.mic_require_wake_word,
        wake_now=req.wake_now,
    )
    return {"robot": scout.get_status()}


@app.post("/api/config")
async def update_config(cfg: ConfigUpdate):
    status = await scout.configure(scout_ip=cfg.scout_ip, mock_mode=cfg.mock_mode)
    return {"robot": status, "ai": ai_brain.get_config()}


@app.post("/api/scan")
async def scan_network(subnet: Optional[str] = None):
    found = await scan_network_for_scout(subnet)
    return {"found": found}


@app.post("/api/move")
async def move_robot(cmd: MoveCommand):
    status = await scout.move(cmd.linear_x, cmd.linear_y, cmd.angular_z, cmd.duration_s)
    return {"robot": status}


@app.post("/api/stop")
async def stop_robot():
    status = await scout.stop()
    return {"robot": status}


@app.post("/api/night-vision")
async def toggle_ir(enable: Optional[bool] = None):
    status = await scout.toggle_night_vision(enable)
    return {"robot": status}


@app.post("/api/ai/provider")
async def change_ai_provider(req: AIProviderUpdate):
    config = ai_brain.set_provider(req.provider, req.model)
    return {"ai": config}


@app.get("/api/audio/tts")
async def get_audio_tts(text: Optional[str] = None):
    """
    Endpoint de áudio streaming MP3.
    Permite reproduzir a voz neural do Scout diretamente nas colunas do computador ou telemóvel.
    """
    if not text:
        if scout.last_speech_mp3:
            return Response(content=scout.last_speech_mp3, media_type="audio/mpeg")
        return Response(content=b"", status_code=400)
    mp3 = await scout.synthesize_speech_mp3(text)
    if not mp3:
        return Response(content=b"", status_code=500)
    return Response(
        content=mp3,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=3600", "Accept-Ranges": "bytes"},
    )


@app.post("/api/ai/chat")
async def chat_with_scout(req: ChatRequest):
    result = await _process_interaction_with_biometrics(
        raw_text=req.prompt,
        cleaned_prompt=req.prompt,
        norm_pcm=scout.last_voice_pcm,
        source_tag="web_chat",
        source_label="Web App",
        speak_reply=req.speak_on_scout,
    )
    reply_text = result.get("reply", "")
    audio_url = f"/api/audio/tts?text={urllib.parse.quote(reply_text)}" if reply_text else None
    return {
        **result,
        "reply": reply_text,
        "audio_url": audio_url,
        "robot": scout.get_status(),
        "identity": identity_memory.get_summary(),
    }


@app.post("/api/speak")
async def speak_on_scout(req: SpeakRequest):
    record_message_log("ha_tts", "📢 Voz Direta (Home Assistant / Web)", req.text, "spoken")
    asyncio.create_task(scout.speak_on_robot(req.text))
    return {
        "status": "speaking",
        "text": req.text,
        "audio_url": f"/api/audio/tts?text={urllib.parse.quote(req.text)}",
    }


async def mjpeg_generator():
    """Gera stream MJPEG continuo para o navegador, telemovel Android e Smartwatch."""
    while True:
        frame = scout.get_camera_jpeg_bytes()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
        await asyncio.sleep(0.12)


@app.get("/api/video.mjpeg")
async def video_stream():
    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/snapshot.jpg")
async def snapshot_jpeg():
    return Response(
        content=scout.get_camera_jpeg_bytes(),
        media_type="image/jpeg",
    )


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket):
    """WebSocket de baixa latencia para Joystick Virtual (Android) e Botoes Rapidos (Galaxy Watch)."""
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action")
            if action == "move":
                await scout.move(
                    linear_x=float(data.get("x", 0.0)),
                    linear_y=float(data.get("y", 0.0)),
                    angular_z=float(data.get("z", 0.0)),
                )
            elif action == "stop":
                await scout.stop()
            elif action == "ir":
                await scout.toggle_night_vision()
            elif action == "xbox_state":
                xbox_controller.update_from_browser_gamepad(data)
            await ws.send_json({"robot": scout.get_status(), "ai": ai_brain.get_config()})
    except WebSocketDisconnect:
        await scout.stop()


# Rotas da Web App (PC / Android PWA) e Painel Home Assistant (/ha)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOTS_DIR)), name="snapshots")


@app.get("/")
async def index_page():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/ha")
async def home_assistant_page():
    return FileResponse(str(FRONTEND_DIR / "ha.html"))


@app.get("/manifest.json")
async def pwa_manifest():
    return FileResponse(str(FRONTEND_DIR / "manifest.json"))
