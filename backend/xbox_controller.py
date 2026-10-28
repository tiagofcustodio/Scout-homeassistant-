import asyncio
import ctypes
import math
import threading
import time
from typing import Any, Dict, List, Optional


class _XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XInputState(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", _XInputGamepad),
    ]


# Mascaras de botoes XInput (Xbox Controller padrao Windows)
XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
XINPUT_GAMEPAD_DPAD_LEFT = 0x0004
XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008
XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_BACK = 0x0020
XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100   # LB
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200  # RB
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_X = 0x4000
XINPUT_GAMEPAD_Y = 0x8000


def _apply_deadzone(val: int, deadzone: int = 7800) -> float:
    if abs(val) < deadzone:
        return 0.0
    sign = 1.0 if val > 0 else -1.0
    norm = (abs(val) - deadzone) / float(32767 - deadzone)
    return round(sign * min(1.0, norm), 3)


class XboxControllerManager:
    """
    Controlador Nativo de Comando Xbox (Windows XInput via ctypes + Web Gamepad API) para o Moorebot Scout.
    Funciona diretamente no PC / Mini PC HP mesmo com o navegador minimizado!
    Mapeamento Mecanum Completo:
    - Analogico Esquerdo (LX / LY): Frente/Tras (linear_x) + Strafe Mecanum Esq/Dir (linear_y)
    - Analogico Direito (RX) ou Gatilhos (LT / RT): Rotacao (angular_z)
    - Setas (D-Pad): Movimento preciso Frente/Tras/Strafe
    - LB / RB: Mudanca de velocidade (40% Lento | 70% Normal | 100% Turbo)
    - Botao A: Saudacao de voz na coluna do Scout
    - Botao B: PARAGEM DE EMERGENCIA (Stop Motores + Stop Patrulha)
    - Botao X: Ligar / Desligar Visao Noturna IR
    - Botao Y: Iniciar / Parar Patrulha e Mapeamento Autonomo
    - Start: Acordar Microfone do Scout por 30s
    """

    def __init__(self, scout: Any, patrol_mapper: Any):
        self.scout = scout
        self.patrol_mapper = patrol_mapper
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._xinput = self._load_xinput_dll()

        self.connected: bool = False
        self.source: str = "Desligado"  # "XInput Nativo (PC)" | "Browser Gamepad API" | "Desligado"
        self.controller_index: int = 0
        self.speed_gear_pct: int = 75   # 40% | 75% | 100%
        self.lx: float = 0.0
        self.ly: float = 0.0
        self.rx: float = 0.0
        self.lt: float = 0.0
        self.rt: float = 0.0
        self.active_buttons: List[str] = []
        self.last_command: str = "A aguardar Comando Xbox..."

        self._was_moving: bool = False
        self._prev_buttons: int = 0
        self._browser_override_until: float = 0.0
        self._stop_worker = False

        threading.Thread(target=self._xinput_poll_loop, daemon=True).start()

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._main_loop = loop

    def _load_xinput_dll(self):
        for dll_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                return getattr(ctypes.windll, dll_name)
            except Exception:
                continue
        return None

    def _xinput_poll_loop(self):
        """Le o comando Xbox ligado por USB/Bluetooth ao PC a 25 Hz via XInput nativo."""
        state = _XInputState()
        while not self._stop_worker:
            if not self._xinput or time.time() < self._browser_override_until:
                time.sleep(0.08)
                continue

            found_idx = -1
            for idx in range(4):
                try:
                    res = self._xinput.XInputGetState(idx, ctypes.byref(state))
                    if res == 0:  # ERROR_SUCCESS
                        found_idx = idx
                        break
                except Exception:
                    pass

            if found_idx == -1:
                if self.source == "XInput Nativo (PC)":
                    self.connected = False
                    self.source = "Desligado"
                    self.active_buttons = []
                time.sleep(0.5)
                continue

            self.connected = True
            self.source = "XInput Nativo (PC)"
            self.controller_index = found_idx

            gp = state.Gamepad
            btns = int(gp.wButtons)
            newly_pressed = btns & (~self._prev_buttons)
            self._prev_buttons = btns

            # Eixos analogicos normalizados [-1.0, +1.0]
            self.lx = _apply_deadzone(gp.sThumbLX)
            self.ly = _apply_deadzone(gp.sThumbLY)
            self.rx = _apply_deadzone(gp.sThumbRX)
            self.lt = round(gp.bLeftTrigger / 255.0, 2) if gp.bLeftTrigger > 20 else 0.0
            self.rt = round(gp.bRightTrigger / 255.0, 2) if gp.bRightTrigger > 20 else 0.0

            # D-Pad override para movimentos precisos
            if btns & XINPUT_GAMEPAD_DPAD_UP:
                self.ly = 0.85
            elif btns & XINPUT_GAMEPAD_DPAD_DOWN:
                self.ly = -0.85
            if btns & XINPUT_GAMEPAD_DPAD_LEFT:
                self.lx = -0.85
            elif btns & XINPUT_GAMEPAD_DPAD_RIGHT:
                self.lx = 0.85

            # Lista legivel de botoes premidos
            btn_names = []
            if btns & XINPUT_GAMEPAD_A: btn_names.append("A")
            if btns & XINPUT_GAMEPAD_B: btn_names.append("B")
            if btns & XINPUT_GAMEPAD_X: btn_names.append("X")
            if btns & XINPUT_GAMEPAD_Y: btn_names.append("Y")
            if btns & XINPUT_GAMEPAD_LEFT_SHOULDER: btn_names.append("LB")
            if btns & XINPUT_GAMEPAD_RIGHT_SHOULDER: btn_names.append("RB")
            if btns & XINPUT_GAMEPAD_START: btn_names.append("START")
            self.active_buttons = btn_names

            # Acoes de clique unico nos botoes
            if newly_pressed:
                self._handle_button_edges(newly_pressed)

            # Calcular velocidades Mecanum (linear_x, linear_y, angular_z)
            scale = (self.speed_gear_pct / 100.0) * 0.48
            rot_scale = (self.speed_gear_pct / 100.0) * 0.90

            lin_x = round(self.ly * scale, 2)
            # No ROS Mecanum: +linear_y = Esquerda, -linear_y = Direita
            lin_y = round(-self.lx * scale, 2)
            # Rotacao combina Analogico Direito X + Gatilhos (LT roda Esq, RT roda Dir)
            rot_input = -self.rx + (self.lt - self.rt)
            ang_z = round(max(-1.0, min(1.0, rot_input)) * rot_scale, 2)

            is_moving_now = abs(lin_x) > 0.01 or abs(lin_y) > 0.01 or abs(ang_z) > 0.01
            if is_moving_now:
                # Se o utilizador mexer no comando Xbox durante patrulha, assume controlo manual
                if self.patrol_mapper.patrolling and self._main_loop:
                    asyncio.run_coroutine_threadsafe(self.patrol_mapper.stop_patrol(), self._main_loop)
                self._was_moving = True
                self.last_command = f"🕹️ Vx={lin_x} Vy={lin_y} Wz={ang_z} ({self.speed_gear_pct}%)"
                if self._main_loop:
                    asyncio.run_coroutine_threadsafe(
                        self.scout.move(lin_x, lin_y, ang_z, 0.0),
                        self._main_loop,
                    )
            elif self._was_moving:
                self._was_moving = False
                self.last_command = "🕹️ Comando Xbox em repouso (Parado)"
                if self._main_loop:
                    asyncio.run_coroutine_threadsafe(self.scout.stop(), self._main_loop)

            time.sleep(0.04)  # 25 Hz

    def _handle_button_edges(self, newly_pressed: int):
        if newly_pressed & XINPUT_GAMEPAD_LEFT_SHOULDER:
            self.speed_gear_pct = 40 if self.speed_gear_pct <= 75 else 75
            self.last_command = f"⚙️ Velocidade reduzida para {self.speed_gear_pct}%"
        elif newly_pressed & XINPUT_GAMEPAD_RIGHT_SHOULDER:
            self.speed_gear_pct = 100 if self.speed_gear_pct >= 75 else 75
            self.last_command = f"🚀 Velocidade aumentada para {self.speed_gear_pct}%"

        if newly_pressed & XINPUT_GAMEPAD_B:
            self.last_command = "🛑 PARAGEM DE EMERGÊNCIA (Botão B)"
            if self._main_loop:
                asyncio.run_coroutine_threadsafe(self.patrol_mapper.stop_patrol(), self._main_loop)
                asyncio.run_coroutine_threadsafe(self.scout.stop(), self._main_loop)

        if newly_pressed & XINPUT_GAMEPAD_X:
            self.last_command = "🌙 Alternar Visão Noturna IR (Botão X)"
            if self._main_loop:
                asyncio.run_coroutine_threadsafe(self.scout.toggle_night_vision(), self._main_loop)

        if newly_pressed & XINPUT_GAMEPAD_Y:
            if self.patrol_mapper.patrolling:
                self.last_command = "⏹️ Parar Patrulha Autónoma (Botão Y)"
                if self._main_loop:
                    asyncio.run_coroutine_threadsafe(self.patrol_mapper.stop_patrol(), self._main_loop)
            else:
                self.last_command = "🚓 Iniciar Patrulha & Mapeamento (Botão Y)"
                if self._main_loop:
                    asyncio.run_coroutine_threadsafe(self.patrol_mapper.start_patrol(), self._main_loop)

        if newly_pressed & XINPUT_GAMEPAD_A:
            self.last_command = "🔊 Saudação de Voz (Botão A)"
            if self._main_loop:
                asyncio.run_coroutine_threadsafe(
                    self.scout.speak_on_robot("Scout pronto às tuas ordens!"),
                    self._main_loop,
                )

        if newly_pressed & XINPUT_GAMEPAD_START:
            self.last_command = "🎤 Microfone do Scout Acordado 30s (START)"
            self.scout.set_mic_vad(True, wake_now=True)

    def update_from_browser_gamepad(self, data: Dict[str, Any]):
        """Atualiza estado quando o comando Xbox e lido pelo navegador (HTML5 Gamepad API)."""
        self._browser_override_until = time.time() + 1.5
        self.connected = bool(data.get("connected", True))
        self.source = "Gamepad API (Browser)"
        self.lx = round(float(data.get("lx", 0.0)), 2)
        self.ly = round(float(data.get("ly", 0.0)), 2)
        self.rx = round(float(data.get("rx", 0.0)), 2)
        self.active_buttons = data.get("buttons", [])
        if data.get("last_command"):
            self.last_command = data["last_command"]

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "source": self.source,
            "controller_index": self.controller_index,
            "speed_gear_pct": self.speed_gear_pct,
            "lx": self.lx,
            "ly": self.ly,
            "rx": self.rx,
            "lt": self.lt,
            "rt": self.rt,
            "active_buttons": self.active_buttons,
            "last_command": self.last_command,
        }
