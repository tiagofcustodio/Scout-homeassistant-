import asyncio
import base64
import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = BASE_DIR / "profiles" / "snapshots"
SNAPSHOTS_META_FILE = SNAPSHOTS_DIR / "snapshots_meta.json"


PERSONALITIES: Dict[str, Dict[str, str]] = {
    "friendly": {
        "id": "friendly",
        "name": "🤖 Companheiro Amigável",
        "badge": "Amigável",
        "prompt": (
            "A tua personalidade atual é COMPANHEIRO AMIGÁVEL: és simpático, prestativo, caloroso "
            "e prestas atenção ao bem-estar da família em casa."
        ),
        "greeting": "Olá! Estou pronto para ajudar aí em casa!",
    },
    "butler": {
        "id": "butler",
        "name": "🎩 Mordomo Jarvis",
        "badge": "Mordomo",
        "prompt": (
            "A tua personalidade atual é MORDOMO BRITÂNICO (estilo J.A.R.V.I.S.): falas com extrema "
            "elegância, cortesia refinada e um leve humor seco inteligente. Tratas a pessoa com "
            "deferência ('Com todo o gosto', 'Às suas ordens')."
        ),
        "greeting": "Ao seu inteiro dispor. A residência encontra-se em perfeita ordem.",
    },
    "sentinel": {
        "id": "sentinel",
        "name": "🛡️ Sentinela Tático",
        "badge": "Sentinela",
        "prompt": (
            "A tua personalidade atual é ROBÔ SENTINELA TÁTICO: falas em estilo operacional conciso "
            "de ficção científica ('Afirmativo', 'Perímetro seguro', 'Sensores óticos operacionais')."
        ),
        "greeting": "Sistemas táticos online. Sensores laser e óticos prontos para patrulha.",
    },
    "comedian": {
        "id": "comedian",
        "name": "😂 Comediante Sarcástico",
        "badge": "Comediante",
        "prompt": (
            "A tua personalidade atual é COMEDIANTE BEM-HUMORADO: fazes comentários engraçados e "
            "espirituosos sobre a vida doméstica e sobre o facto de seres um pequeno robô de quatro "
            "rodas Mecanum, mas cumpres sempre o que te pedem."
        ),
        "greeting": "Cá estou eu, com os meus doze centímetros de pura inteligência artificial!",
    },
    "pet": {
        "id": "pet",
        "name": "🐾 Mascote Curioso",
        "badge": "Mascote",
        "prompt": (
            "A tua personalidade atual é MASCOTE ROBÓTICO CURIOSO: és super entusiasmado, energético, "
            "carinhoso e adoras explorar cada canto da sala e seguir as pessoas da casa."
        ),
        "greeting": "Iupi! Vamos brincar ou explorar a sala juntos?",
    },
}


class DomesticFeaturesManager:
    """
    Funcionalidades domésticas avançadas e divertidas para o Moorebot Scout:
    1. Motor de Personalidades de IA (Companheiro, Mordomo Jarvis, Sentinela, Comediante, Mascote).
    2. Coreografias e Acrobacias Mecanum (Dançar, Saudação, Pirueta 360, Festejar, Olhar em Redor).
    3. Modo Sentinela / Alarme Residencial (deteção de movimento na câmara + barreira Laser ToF).
    4. Galeria de Fotografias (Photo Booth & Snapshots de Segurança).
    5. Temporizadores e Lembretes Falados na coluna física do Scout.
    """

    def __init__(self, scout, identity_memory, log_callback: Optional[Callable] = None):
        self.scout = scout
        self.identity_memory = identity_memory
        self.log_callback = log_callback

        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        self.personality_id: str = "friendly"

        # Estado de Coreografias
        self.active_stunt: Optional[str] = None

        # Estado do Modo Sentinela (Vigilância / Alarme)
        self.sentry_enabled: bool = False
        self.sentry_alarm_sound: bool = True
        self.sentry_motion_score: float = 0.0
        self.sentry_triggers_count: int = 0
        self.sentry_last_event: str = "Desativado"
        self._sentry_task: Optional[asyncio.Task] = None
        self._prev_gray_thumb: Optional[List[int]] = None
        self._prev_tof_m: Optional[float] = None
        self._last_sentry_alert_ts: float = 0.0

        # Temporizadores / Lembretes Falados
        self.timers: List[Dict] = []
        self._timer_counter: int = 0

        # Galeria de Fotografias
        self.snapshots: List[Dict] = []
        self._load_snapshots_meta()

    def _log(self, source: str, sender: str, text: str, status: str = "info"):
        if self.log_callback:
            try:
                self.log_callback(source, sender, text, status)
            except Exception:
                pass

    # =========================================================================
    # 1. PERSONALIDADES DE IA
    # =========================================================================
    def set_personality(self, personality_id: str, speak_greeting: bool = True) -> Dict:
        if personality_id not in PERSONALITIES:
            personality_id = "friendly"
        self.personality_id = personality_id
        p = PERSONALITIES[personality_id]
        self._log("system", "🎭 Personalidade IA", f"Modo alterado para {p['name']}", "personality")
        if speak_greeting:
            asyncio.create_task(self.scout.speak_on_robot(p["greeting"]))
        return self.get_personality()

    def get_personality(self) -> Dict:
        p = PERSONALITIES.get(self.personality_id, PERSONALITIES["friendly"])
        return {
            "active_id": self.personality_id,
            "name": p["name"],
            "badge": p["badge"],
            "prompt": p["prompt"],
            "available": [
                {"id": k, "name": v["name"], "badge": v["badge"]}
                for k, v in PERSONALITIES.items()
            ],
        }

    # =========================================================================
    # 2. COREOGRAFIAS E ACROBACIAS MECANUM (STUNTS & EMOTES)
    # =========================================================================
    async def perform_stunt(self, stunt_name: str) -> Dict:
        if self.active_stunt:
            return {"ok": False, "error": "Já está a executar uma coreografia"}

        self.active_stunt = stunt_name
        asyncio.create_task(self._run_stunt_sequence(stunt_name))
        return {"ok": True, "stunt": stunt_name}

    async def _run_stunt_sequence(self, stunt_name: str):
        try:
            if stunt_name == "dance":
                self._log("system", "💃 Coreografia Scout", "A executar Dança Mecanum!", "stunt")
                asyncio.create_task(self.scout.speak_on_robot("Olha só o meu ritmo nas rodas Mecanum!"))
                # Strafe esquerda / direita + diagonais + giro
                await self.scout.move(0.0, 0.38, 0.0, 0.55)
                await self.scout.move(0.0, -0.38, 0.0, 0.55)
                await self.scout.move(0.25, 0.25, 0.5, 0.6)
                await self.scout.move(-0.25, -0.25, -0.5, 0.6)
                await self.scout.move(0.0, 0.0, 1.1, 1.2)
                await self.scout.move(0.0, 0.0, -1.1, 1.2)
                await self.scout.stop()

            elif stunt_name == "salute":
                self._log("system", "🫡 Coreografia Scout", "Saudação ao comandante!", "stunt")
                asyncio.create_task(self.scout.speak_on_robot("Scout pronto e às tuas ordens!"))
                await self.scout.move(0.28, 0.0, 0.0, 0.45)
                await asyncio.sleep(0.15)
                await self.scout.move(-0.28, 0.0, 0.0, 0.45)
                await self.scout.move(0.0, 0.0, 0.8, 0.35)
                await self.scout.move(0.0, 0.0, -0.8, 0.35)
                await self.scout.stop()

            elif stunt_name == "spin360":
                self._log("system", "🌀 Coreografia Scout", "Pirueta 360 graus!", "stunt")
                asyncio.create_task(self.scout.speak_on_robot("Uau, lá vou eu numa volta completa!"))
                await self.scout.move(0.0, 0.0, 1.25, 2.3)
                await self.scout.stop()

            elif stunt_name == "celebrate":
                self._log("system", "🎉 Coreografia Scout", "Modo festejo ativado!", "stunt")
                asyncio.create_task(self.scout.speak_on_robot("Iupi! Vitória! Somos a melhor equipa!"))
                for _ in range(3):
                    await self.scout.move(0.0, 0.35, 0.8, 0.35)
                    await self.scout.move(0.0, -0.35, -0.8, 0.35)
                await self.scout.stop()

            elif stunt_name == "scan_room":
                self._log("system", "🔍 Coreografia Scout", "A inspecionar a divisão...", "stunt")
                asyncio.create_task(self.scout.speak_on_robot("A analisar o perímetro da divisão."))
                await self.scout.move(0.0, 0.0, 0.65, 0.8)
                await asyncio.sleep(0.4)
                await self.scout.move(0.0, 0.0, -0.65, 1.6)
                await asyncio.sleep(0.4)
                await self.scout.move(0.0, 0.0, 0.65, 0.8)
                await self.scout.stop()

            elif stunt_name == "wiggle":
                self._log("system", "🐾 Coreografia Scout", "A abanar de alegria!", "stunt")
                asyncio.create_task(self.scout.speak_on_robot("Que bom ver-te por aqui!"))
                for _ in range(4):
                    await self.scout.move(0.0, 0.32, 0.0, 0.22)
                    await self.scout.move(0.0, -0.32, 0.0, 0.22)
                await self.scout.stop()
        finally:
            self.active_stunt = None

    # =========================================================================
    # 3. GALERIA DE FOTOGRAFIAS (PHOTO BOOTH & SNAPSHOTS)
    # =========================================================================
    def _load_snapshots_meta(self):
        if SNAPSHOTS_META_FILE.exists():
            try:
                data = json.loads(SNAPSHOTS_META_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.snapshots = data[-30:]
            except Exception:
                self.snapshots = []

    def _save_snapshots_meta(self):
        try:
            SNAPSHOTS_META_FILE.write_text(
                json.dumps(self.snapshots[-30:], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def take_snapshot(self, label: str = "Foto Manual", speak_shutter: bool = True) -> Dict:
        jpeg_bytes = self.scout.get_camera_jpeg_bytes()
        now_dt = datetime.now()
        snap_id = f"snap_{now_dt.strftime('%Y%m%d_%H%M%S')}_{int((time.time() % 1) * 1000):03d}"
        filename = f"{snap_id}.jpg"
        filepath = SNAPSHOTS_DIR / filename

        # Identifica se está alguém conhecido na foto
        person_name = None
        try:
            id_res = self.identity_memory.identify(jpeg_bytes=jpeg_bytes, pcm_16k_bytes=None)
            person_name = id_res.get("active_person")
        except Exception:
            pass

        try:
            filepath.write_bytes(jpeg_bytes)
        except Exception:
            pass

        # Cria miniatura base64 leve para exibição instantânea na galeria UI
        thumb_b64 = ""
        try:
            img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
            img.thumbnail((320, 180))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=78)
            thumb_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            pass

        entry = {
            "id": snap_id,
            "filename": filename,
            "timestamp": now_dt.strftime("%H:%M:%S · %d/%m"),
            "label": label,
            "person": person_name,
            "tof_m": round(float(self.scout.telemetry.tof_range_m), 2),
            "thumb_b64": thumb_b64,
        }
        self.snapshots.append(entry)
        if len(self.snapshots) > 24:
            old = self.snapshots.pop(0)
            old_file = SNAPSHOTS_DIR / old.get("filename", "")
            if old_file.exists():
                try:
                    old_file.unlink()
                except Exception:
                    pass

        self._save_snapshots_meta()
        who_str = f" ({person_name})" if person_name else ""
        self._log("system", "📸 Câmara Scout", f"Fotografia guardada: {label}{who_str}", "snapshot")
        if speak_shutter:
            msg = f"Sorri{', ' + person_name if person_name else ''}! Fotografia capturada!"
            asyncio.create_task(self.scout.speak_on_robot(msg))
        return entry

    def delete_snapshot(self, snap_id: str) -> bool:
        for idx, s in enumerate(self.snapshots):
            if s.get("id") == snap_id:
                self.snapshots.pop(idx)
                f = SNAPSHOTS_DIR / s.get("filename", "")
                if f.exists():
                    try:
                        f.unlink()
                    except Exception:
                        pass
                self._save_snapshots_meta()
                return True
        return False

    # =========================================================================
    # 4. MODO SENTINELA / ALARME RESIDENCIAL INTELIGENTE
    # =========================================================================
    def toggle_sentry(self, enabled: Optional[bool] = None, alarm_sound: Optional[bool] = None) -> Dict:
        if enabled is None:
            self.sentry_enabled = not self.sentry_enabled
        else:
            self.sentry_enabled = bool(enabled)

        if alarm_sound is not None:
            self.sentry_alarm_sound = bool(alarm_sound)

        if self.sentry_enabled:
            self._prev_gray_thumb = None
            self._prev_tof_m = None
            self.sentry_last_event = "🟢 A vigiar perímetro (Câmara + Laser ToF)"
            self._log("system", "🛡️ Modo Sentinela", "Vigilância residencial ATIVADA.", "sentry")
            asyncio.create_task(
                self.scout.speak_on_robot("Modo sentinela ativado. Estou a vigiar a divisão.")
            )
            if not self._sentry_task or self._sentry_task.done():
                self._sentry_task = asyncio.create_task(self._sentry_loop())
        else:
            self.sentry_last_event = "⚪ Desativado"
            self.sentry_motion_score = 0.0
            self._log("system", "🛡️ Modo Sentinela", "Vigilância residencial DESATIVADA.", "sentry")
            asyncio.create_task(self.scout.speak_on_robot("Modo sentinela desativado."))

        return self.get_status()

    async def _sentry_loop(self):
        while self.sentry_enabled:
            try:
                jpeg_bytes = self.scout.get_camera_jpeg_bytes()
                img = Image.open(io.BytesIO(jpeg_bytes)).convert("L").resize((64, 36))
                pixels = list(img.getdata())

                motion_pct = 0.0
                if self._prev_gray_thumb is not None and len(pixels) == len(self._prev_gray_thumb):
                    diff = sum(abs(a - b) for a, b in zip(pixels, self._prev_gray_thumb)) / float(len(pixels))
                    motion_pct = round(min(100.0, (diff / 28.0) * 100.0), 1)
                self._prev_gray_thumb = pixels
                self.sentry_motion_score = motion_pct

                cur_tof = float(self.scout.telemetry.tof_range_m)
                tof_jump = False
                if self._prev_tof_m is not None and 0.05 < cur_tof < 1.5:
                    if abs(cur_tof - self._prev_tof_m) > 0.35:
                        tof_jump = True
                self._prev_tof_m = cur_tof

                now = time.time()
                # Se detetar movimento visual significativo (> 24%) ou corte do feixe laser ToF
                if (motion_pct >= 24.0 or tof_jump) and (now - self._last_sentry_alert_ts > 12.0):
                    self._last_sentry_alert_ts = now
                    self.sentry_triggers_count += 1
                    cause = "Movimento Ótico" if motion_pct >= 24.0 else f"Barreira Laser ({cur_tof:.2f}m)"

                    # Tenta identificar quem é
                    id_res = await asyncio.to_thread(
                        self.identity_memory.identify, jpeg_bytes, None
                    )
                    person = id_res.get("active_person")
                    conf = id_res.get("active_confidence_pct", 0)

                    if person and conf >= 58:
                        self.sentry_last_event = f"✅ {person} detetado ({cause})"
                        self.take_snapshot(label=f"Sentinela: {person}", speak_shutter=False)
                        if self.sentry_alarm_sound:
                            asyncio.create_task(
                                self.scout.speak_on_robot(f"Olá {person}! Movimento registado pelo modo sentinela.")
                            )
                    else:
                        self.sentry_last_event = f"🚨 Alerta! Movimento ({cause})"
                        self.take_snapshot(label=f"🚨 Alerta Sentinela ({cause})", speak_shutter=False)
                        if self.sentry_alarm_sound:
                            asyncio.create_task(
                                self.scout.speak_on_robot(
                                    "Atenção! Movimento detetado na área vigiada! Fotografia de segurança registada."
                                )
                            )
            except Exception:
                pass
            await asyncio.sleep(0.8)

    # =========================================================================
    # 5. TEMPORIZADORES E LEMBRETES FALADOS NO ROBÔ
    # =========================================================================
    def add_timer(self, seconds: int, label: str = "Alarme do Scout") -> Dict:
        seconds = max(5, min(7200, int(seconds)))
        self._timer_counter += 1
        timer_id = f"tmr_{self._timer_counter}"
        target_ts = time.time() + seconds
        item = {
            "id": timer_id,
            "label": label.strip() or "Alarme do Scout",
            "duration_s": seconds,
            "target_ts": target_ts,
            "active": True,
        }
        self.timers.append(item)
        mins_str = f"{seconds // 60} min" if seconds >= 60 else f"{seconds} seg"
        self._log("system", "⏰ Temporizador Scout", f"Criado: '{item['label']}' ({mins_str})", "timer")
        asyncio.create_task(self._run_timer(item))
        return item

    async def _run_timer(self, item: Dict):
        wait_s = max(0.0, item["target_ts"] - time.time())
        await asyncio.sleep(wait_s)
        if not item.get("active"):
            return
        item["active"] = False
        msg = f"Atenção! O temporizador para {item['label']} terminou!"
        self._log("ai_reply", "⏰ Alarme do Scout", msg, "timer_done")
        await self.scout.speak_on_robot(msg)

    def cancel_timer(self, timer_id: str) -> bool:
        for t in self.timers:
            if t["id"] == timer_id:
                t["active"] = False
        self.timers = [t for t in self.timers if t.get("active")]
        return True

    def get_status(self) -> Dict:
        now = time.time()
        active_timers = []
        for t in self.timers:
            if t.get("active"):
                rem = int(max(0, round(t["target_ts"] - now)))
                if rem > 0:
                    active_timers.append({
                        "id": t["id"],
                        "label": t["label"],
                        "remaining_s": rem,
                    })
                else:
                    t["active"] = False

        return {
            "personality": self.get_personality(),
            "active_stunt": self.active_stunt,
            "sentry": {
                "enabled": self.sentry_enabled,
                "alarm_sound": self.sentry_alarm_sound,
                "motion_score_pct": self.sentry_motion_score,
                "triggers_count": self.sentry_triggers_count,
                "last_event": self.sentry_last_event,
            },
            "timers": active_timers,
            "snapshots": list(reversed(self.snapshots[-12:])),
        }
