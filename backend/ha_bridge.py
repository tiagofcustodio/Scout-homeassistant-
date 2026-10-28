import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
HA_CONFIG_FILE = BASE_DIR / "profiles" / "ha_config.json"


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )


class HomeAssistantBridge:
    """
    Ponte Bidirecional entre o Moorebot Scout (Mini PC) e o Home Assistant:
    1. Descobre automaticamente todos os dispositivos da casa (luzes, tomadas, interruptores,
       estores, ar condicionado, televisao, cenas e scripts) via REST API do Home Assistant.
    2. Permite que o Scout controle qualquer dispositivo da casa por comando de voz em Portugues
       (ex: 'Scout, acende a luz da sala', 'Apaga o candeeiro do quarto', 'Fecha os estores').
    3. Permite que o Home Assistant envie mensagens de voz (TTS), comandos de movimento e
       consulte os sensores e a pessoa identificada pelo Scout.
    """

    CONTROLLABLE_DOMAINS = {
        "light", "switch", "cover", "climate", "media_player", "fan", "scene", "script", "input_boolean", "lock"
    }

    def __init__(self):
        self.ha_url: str = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip("/")
        self.ha_token: str = os.getenv("HA_TOKEN", "").strip()
        self.connected: bool = False
        self.last_error: str = ""
        self._entities_cache: List[Dict[str, Any]] = []
        self._last_sync_ts: float = 0.0
        self._load_saved_config()

    def _load_saved_config(self):
        if HA_CONFIG_FILE.exists():
            try:
                with open(HA_CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if cfg.get("ha_url"):
                        self.ha_url = cfg["ha_url"].rstrip("/")
                    if cfg.get("ha_token"):
                        self.ha_token = cfg["ha_token"].strip()
            except Exception:
                pass

    def save_config(self, ha_url: str, ha_token: str) -> Dict[str, Any]:
        self.ha_url = ha_url.strip().rstrip("/")
        if ha_token.strip():
            self.ha_token = ha_token.strip()
        HA_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(HA_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"ha_url": self.ha_url, "ha_token": self.ha_token}, f, indent=2)
        except Exception:
            pass
        self._last_sync_ts = 0.0
        return self.get_status()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json",
        }

    async def sync_entities(self, force: bool = False) -> List[Dict[str, Any]]:
        """Obtém a lista de dispositivos controláveis do Home Assistant."""
        if not self.ha_token:
            self.connected = False
            self.last_error = "Token do Home Assistant (HA_TOKEN) ainda não configurado"
            return []

        if not force and (time.time() - self._last_sync_ts < 15.0) and self._entities_cache:
            return self._entities_cache

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.ha_url}/api/states", headers=self._headers())
                resp.raise_for_status()
                states = resp.json()
                devices: List[Dict[str, Any]] = []
                for st in states:
                    ent_id = st.get("entity_id", "")
                    domain = ent_id.split(".")[0] if "." in ent_id else ""
                    if domain not in self.CONTROLLABLE_DOMAINS:
                        continue
                    attrs = st.get("attributes", {})
                    friendly_name = attrs.get("friendly_name", ent_id)
                    devices.append(
                        {
                            "entity_id": ent_id,
                            "domain": domain,
                            "name": friendly_name,
                            "state": st.get("state", "unknown"),
                        }
                    )
                self._entities_cache = sorted(devices, key=lambda x: (x["domain"], x["name"]))
                self._last_sync_ts = time.time()
                self.connected = True
                self.last_error = ""
                return self._entities_cache
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            return self._entities_cache

    async def call_service(self, domain: str, service: str, entity_id: str, extra_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executa um serviço no Home Assistant (ex: light.turn_on, switch.turn_off, cover.close_cover)."""
        if not self.ha_token:
            return {"ok": False, "error": "Token do Home Assistant não configurado."}

        payload: Dict[str, Any] = {"entity_id": entity_id}
        if extra_data:
            payload.update(extra_data)

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.post(
                    f"{self.ha_url}/api/services/{domain}/{service}",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
                # Atualizar cache em background
                self._last_sync_ts = 0.0
                return {
                    "ok": True,
                    "domain": domain,
                    "service": service,
                    "entity_id": entity_id,
                }
        except Exception as e:
            return {"ok": False, "error": str(e), "entity_id": entity_id}

    async def try_execute_voice_home_command(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Analisa se a frase dita ao Scout é um comando para controlar dispositivos da casa no Home Assistant!
        Exemplos suportados:
        - "Acende a luz da sala" / "Liga o candeeiro do quarto" / "Apaga todas as luzes"
        - "Desliga a tomada da televisão" / "Liga o ventilador"
        - "Abre os estores da sala" / "Fecha a persiana do quarto"
        """
        norm = _strip_accents(text)

        # Verificar se contém verbos de ação doméstica + substantivos de dispositivos (ou nome de entidade conhecida)
        turn_on_words = ["acende", "acender", "liga", "ligar", "ativa", "ativar", "abre", "abrir"]
        turn_off_words = ["apaga", "apagar", "desliga", "desligar", "desativa", "desativar", "fecha", "fechar"]
        home_nouns = [
            "luz", "luzes", "candeeiro", "lampada", "fita led", "led", "tomada", "interruptor",
            "televisao", "tv", "estore", "estores", "persiana", "persianas", "ar condicionado",
            "aquecedor", "ventoinha", "ventilador", "portao", "porta", "cena", "quarto", "sala",
            "cozinha", "escritorio", "wc", "casa de banho", "corredor", "varanda", "garagem",
        ]

        # Ignorar comandos internos do próprio Scout ("liga visão noturna", "anda para a frente")
        if any(w in norm for w in ["visao noturna", "infravermelho", "anda", "recua", "vira", "roda"]):
            return None

        is_on = any(re.search(rf"\b{w}\b", norm) for w in turn_on_words)
        is_off = any(re.search(rf"\b{w}\b", norm) for w in turn_off_words)
        has_home_noun = any(n in norm for n in home_nouns)

        if not (is_on or is_off) or not has_home_noun:
            return None

        entities = await self.sync_entities()
        if not entities:
            if not self.ha_token:
                return {
                    "matched": True,
                    "ok": False,
                    "reply": "Percebi que queres controlar um dispositivo da casa, mas ainda precisas de configurar o Token do Home Assistant no painel.",
                }
            return None

        # Procurar a entidade do Home Assistant cujo nome amigável melhor corresponde à frase
        best_ent: Optional[Dict[str, Any]] = None
        best_score = 0.0

        # Remover verbos e artigos para isolar o alvo (ex: "luz da sala")
        stop_tokens = set(turn_on_words + turn_off_words + ["scout", "robo", "robot", "o", "a", "os", "as", "do", "da", "dos", "das", "no", "na", "por", "favor", "podes"])
        query_tokens = [t for t in re.split(r"[^a-z0-9]+", norm) if t and t not in stop_tokens]

        for ent in entities:
            ename_norm = _strip_accents(ent["name"])
            eid_norm = _strip_accents(ent["entity_id"].split(".")[-1].replace("_", " "))
            full_target = f"{ename_norm} {eid_norm}"
            ent_tokens = set(re.split(r"[^a-z0-9]+", full_target))

            overlap = sum(1.5 if qt in ename_norm else (1.0 if qt in ent_tokens else 0.0) for qt in query_tokens)
            # Bonus de domínio
            if ("luz" in norm or "candeeiro" in norm or "lampada" in norm) and ent["domain"] == "light":
                overlap += 1.5
            elif ("estore" in norm or "persiana" in norm) and ent["domain"] == "cover":
                overlap += 1.8
            elif ("tomada" in norm or "interruptor" in norm) and ent["domain"] == "switch":
                overlap += 1.4
            elif ("tv" in norm or "televisao" in norm) and ent["domain"] == "media_player":
                overlap += 1.6

            if overlap > best_score:
                best_score = overlap
                best_ent = ent

        if not best_ent or best_score < 1.4:
            return None

        domain = best_ent["domain"]
        ent_id = best_ent["entity_id"]
        friendly = best_ent["name"]

        if domain == "cover":
            service = "open_cover" if is_on else "close_cover"
            verb_done = "abri" if is_on else "fechei"
        elif domain in ("scene", "script"):
            service = "turn_on"
            verb_done = "ativei"
        else:
            service = "turn_on" if is_on else "turn_off"
            verb_done = "liguei" if is_on else "desliguei"

        res = await self.call_service(domain, service, ent_id)
        if res.get("ok"):
            return {
                "matched": True,
                "ok": True,
                "entity": best_ent,
                "service": f"{domain}.{service}",
                "reply": f"Pronto, já {verb_done} {friendly} no Home Assistant!",
            }
        else:
            return {
                "matched": True,
                "ok": False,
                "reply": f"Tentei controlar {friendly} no Home Assistant, mas ocorreu um erro de ligação.",
            }

    def get_status(self) -> Dict[str, Any]:
        return {
            "ha_url": self.ha_url,
            "has_token": bool(self.ha_token),
            "connected": self.connected,
            "last_error": self.last_error,
            "device_count": len(self._entities_cache),
            "devices": self._entities_cache[:60],
        }
