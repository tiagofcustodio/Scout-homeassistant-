import asyncio
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
MAP_FILE = BASE_DIR / "profiles" / "house_map.json"


class PatrolAndMapper:
    """
    Motor de Patrulha Autonoma e Mapeamento 2D (SLAM Laser ToF + Odometria/IMU) para o Moorebot Scout.
    - Constroi em tempo real um mapa 2D da casa com:
      * Trajetoria percorrida pelo Scout (path)
      * Paredes e obstaculos detetados pelo feixe Laser Time-of-Flight (/SensorNode/tof)
      * Pontos de interesse / Pessoas identificadas no mapa (landmarks)
      * Calculo de area explorada em m²
    - Modo Patrulha Autonoma:
      * Explora a divisao desviando-se de obstaculos com o sensor ToF
      * Faz varrimentos radar (Radar Sweep) para mapear paredes a volta
      * Ativa automaticamente a Visao Noturna IR se a sala estiver escura
    """

    def __init__(self, scout: Any):
        self.scout = scout
        self._lock = threading.Lock()
        self.patrolling: bool = False
        self.patrol_state: str = "Parado"
        self.patrol_started_at: float = 0.0
        self.total_distance_m: float = 0.0

        # Pose estimada combinada (Odometria + Dead-Reckoning de comandos Mecanum)
        self.pose_x: float = 0.0
        self.pose_y: float = 0.0
        self.pose_yaw_rad: float = 0.0
        self._last_odom_x: float = 0.0
        self._last_odom_y: float = 0.0
        self._last_update_ts: float = time.time()

        # Dados do Mapa 2D (resolucao de grelha 0.10m = 10cm)
        self.path: List[Tuple[float, float]] = [(0.0, 0.0)]
        self._obstacle_grid: Dict[Tuple[int, int], int] = {}
        self._free_grid: set[Tuple[int, int]] = {(0, 0)}
        self.landmarks: List[Dict[str, Any]] = [
            {"x": 0.0, "y": 0.0, "label": "🏠 Base Inicial", "ts": int(time.time())}
        ]

        self._stop_worker = False
        self._patrol_task: Optional[asyncio.Task] = None
        self._load_map()

        threading.Thread(target=self._mapping_loop, daemon=True).start()

    def _load_map(self):
        if MAP_FILE.exists():
            try:
                with open(MAP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.path = [tuple(p) for p in data.get("path", [[0.0, 0.0]])][-400:]
                    for gx, gy, count in data.get("obstacles", []):
                        self._obstacle_grid[(int(gx), int(gy))] = int(count)
                    for gx, gy in data.get("free_cells", []):
                        self._free_grid.add((int(gx), int(gy)))
                    self.landmarks = data.get("landmarks", self.landmarks)[-30:]
                    self.total_distance_m = float(data.get("total_distance_m", 0.0))
                    if self.path:
                        self.pose_x, self.pose_y = self.path[-1]
            except Exception:
                pass

    def save_map(self):
        MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock:
                obs_list = [[gx, gy, c] for (gx, gy), c in list(self._obstacle_grid.items())[-800:]]
                free_list = [[gx, gy] for (gx, gy) in list(self._free_grid)[-1500:]]
                payload = {
                    "updated_at": int(time.time()),
                    "total_distance_m": round(self.total_distance_m, 2),
                    "path": [[round(x, 3), round(y, 3)] for x, y in self.path[-400:]],
                    "obstacles": obs_list,
                    "free_cells": free_list,
                    "landmarks": self.landmarks[-30:],
                }
            with open(MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            pass

    def reset_map(self) -> Dict[str, Any]:
        with self._lock:
            self.pose_x = 0.0
            self.pose_y = 0.0
            self.pose_yaw_rad = 0.0
            self.total_distance_m = 0.0
            self.path = [(0.0, 0.0)]
            self._obstacle_grid.clear()
            self._free_grid = {(0, 0)}
            self.landmarks = [{"x": 0.0, "y": 0.0, "label": "🏠 Base Inicial", "ts": int(time.time())}]
        self.save_map()
        return self.get_map_state()

    def add_landmark(self, label: str):
        with self._lock:
            for lm in self.landmarks[-5:]:
                if lm["label"] == label and math.hypot(lm["x"] - self.pose_x, lm["y"] - self.pose_y) < 0.4:
                    return
            self.landmarks.append(
                {
                    "x": round(self.pose_x, 2),
                    "y": round(self.pose_y, 2),
                    "label": label,
                    "ts": int(time.time()),
                }
            )
            if len(self.landmarks) > 30:
                self.landmarks.pop(0)

    def _mapping_loop(self):
        """Atualiza continuamente a pose 2D e projeta o feixe Laser ToF no mapa de ocupacao."""
        last_save = time.time()
        while not self._stop_worker:
            now = time.time()
            dt = min(0.5, max(0.01, now - self._last_update_ts))
            self._last_update_ts = now

            t = self.scout.telemetry
            with self._lock:
                dx_odom = t.odom_x - self._last_odom_x
                dy_odom = t.odom_y - self._last_odom_y
                self._last_odom_x = t.odom_x
                self._last_odom_y = t.odom_y

                if abs(dx_odom) > 0.001 or abs(dy_odom) > 0.001:
                    self.pose_x += dx_odom
                    self.pose_y += dy_odom
                    self.pose_yaw_rad = math.radians(t.odom_yaw_deg)
                    step_dist = math.hypot(dx_odom, dy_odom)
                else:
                    vx = t.linear_x * 0.45
                    vy = t.linear_y * 0.45
                    wz = t.angular_z * 1.15
                    self.pose_yaw_rad += wz * dt
                    self.pose_yaw_rad = (self.pose_yaw_rad + math.pi) % (2 * math.pi) - math.pi
                    dx = (vx * math.cos(self.pose_yaw_rad) - vy * math.sin(self.pose_yaw_rad)) * dt
                    dy = (vx * math.sin(self.pose_yaw_rad) + vy * math.cos(self.pose_yaw_rad)) * dt
                    self.pose_x += dx
                    self.pose_y += dy
                    step_dist = math.hypot(dx, dy)

                if step_dist > 0.002:
                    self.total_distance_m += step_dist
                    last_px, last_py = self.path[-1]
                    if math.hypot(self.pose_x - last_px, self.pose_y - last_py) >= 0.06:
                        self.path.append((round(self.pose_x, 3), round(self.pose_y, 3)))
                        if len(self.path) > 400:
                            self.path.pop(0)

                # Projetar feixe Laser Time-of-Flight (/SensorNode/tof) no mapa (grelha de 10cm)
                tof_d = float(t.tof_distance_m)
                if 0.04 <= tof_d <= 2.0:
                    steps = int(min(tof_d, 1.85) / 0.10)
                    for s in range(steps):
                        r = s * 0.10
                        fx = int(round((self.pose_x + r * math.cos(self.pose_yaw_rad)) / 0.10))
                        fy = int(round((self.pose_y + r * math.sin(self.pose_yaw_rad)) / 0.10))
                        self._free_grid.add((fx, fy))
                        if (fx, fy) in self._obstacle_grid and self._obstacle_grid[(fx, fy)] < 3:
                            del self._obstacle_grid[(fx, fy)]

                    if tof_d < 1.80:
                        ox = int(round((self.pose_x + tof_d * math.cos(self.pose_yaw_rad)) / 0.10))
                        oy = int(round((self.pose_y + tof_d * math.sin(self.pose_yaw_rad)) / 0.10))
                        self._obstacle_grid[(ox, oy)] = min(10, self._obstacle_grid.get((ox, oy), 0) + 1)

            if now - last_save > 20.0 and self.patrolling:
                last_save = now
                self.save_map()

            time.sleep(0.15)

    async def start_patrol(self, on_event_callback: Optional[Callable] = None) -> Dict[str, Any]:
        if self.patrolling:
            return self.get_map_state()

        self.patrolling = True
        self.patrol_started_at = time.time()
        self.patrol_state = "🔍 A iniciar patrulha e mapeamento..."
        self.add_landmark("🚩 Início Patrulha")
        self._patrol_task = asyncio.create_task(self._autonomous_patrol_coroutine(on_event_callback))
        return self.get_map_state()

    async def stop_patrol(self) -> Dict[str, Any]:
        self.patrolling = False
        self.patrol_state = "Parado"
        if self._patrol_task and not self._patrol_task.done():
            self._patrol_task.cancel()
        await self.scout.stop()
        self.save_map()
        return self.get_map_state()

    async def _autonomous_patrol_coroutine(self, on_event_callback: Optional[Callable] = None):
        step_counter = 0
        try:
            if self.scout.telemetry.light_lux < 15.0 and not self.scout.telemetry.ir_led_on:
                await self.scout.toggle_night_vision(True)

            while self.patrolling:
                step_counter += 1
                tof = self.scout.telemetry.tof_distance_m

                if step_counter % 18 == 0:
                    self.patrol_state = "📡 Varrimento Radar 360° (A mapear sala...)"
                    for _ in range(4):
                        if not self.patrolling:
                            break
                        await self.scout.move(0.0, 0.0, 0.65, 0.55)
                        await asyncio.sleep(0.25)
                    continue

                if tof < 0.42:
                    self.patrol_state = f"⚠️ Obstáculo a {tof:.2f}m — A contornar..."
                    await self.scout.stop()
                    await asyncio.sleep(0.15)
                    await self.scout.move(-0.22, 0.0, 0.0, 0.65)
                    await asyncio.sleep(0.15)

                    turn_dir = 0.70 if (step_counter % 2 == 0) else -0.70
                    for _ in range(5):
                        if not self.patrolling:
                            break
                        await self.scout.move(0.0, 0.0, turn_dir, 0.45)
                        await asyncio.sleep(0.15)
                        if self.scout.telemetry.tof_distance_m > 0.72:
                            break
                    continue

                speed = 0.28 if tof > 0.85 else 0.20
                self.patrol_state = f"🚓 A patrulhar (Livre: {tof:.2f}m)"
                await self.scout.move(speed, 0.0, 0.0, 0.50)
                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.patrol_state = f"Erro Patrulha: {e}"
        finally:
            self.patrolling = False
            await self.scout.stop()
            self.save_map()

    def get_map_state(self) -> Dict[str, Any]:
        with self._lock:
            obstacles_xy = [
                [round(gx * 0.10, 2), round(gy * 0.10, 2), count]
                for (gx, gy), count in list(self._obstacle_grid.items())[-500:]
            ]
            free_xy = [
                [round(gx * 0.10, 2), round(gy * 0.10, 2)]
                for (gx, gy) in list(self._free_grid)[-800:]
            ]
            area_m2 = round(len(self._free_grid) * 0.01, 2)
            return {
                "patrolling": self.patrolling,
                "patrol_state": self.patrol_state,
                "pose": {
                    "x": round(self.pose_x, 2),
                    "y": round(self.pose_y, 2),
                    "yaw_deg": round(math.degrees(self.pose_yaw_rad), 1),
                },
                "tof_beam_m": round(self.scout.telemetry.tof_distance_m, 2),
                "total_distance_m": round(self.total_distance_m, 2),
                "explored_area_m2": area_m2,
                "obstacle_count": len(self._obstacle_grid),
                "path": [[round(x, 2), round(y, 2)] for x, y in self.path[-250:]],
                "obstacles": obstacles_xy,
                "free_cells": free_xy,
                "landmarks": list(self.landmarks[-20:]),
            }
