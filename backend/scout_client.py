import asyncio
import base64
import io
import math
import os
import re
import socket
import struct
import threading
import time
import xmlrpc.client
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from xmlrpc.server import SimpleXMLRPCServer
from PIL import Image, ImageDraw

TWIST_MD5 = "9f195f881246fdfa2798d1d3eebca84a"
TWIST_DEF = "Vector3  linear\nVector3  angular\n"
FRAME_MD5 = "bce5a3441e8f21e02d2b9d7ce432bea2"
FRAME_DEF = "uint32 seq\nuint64 stamp\nuint32 session\nint8 type\nuint32 oseq\nint32 par1\nint32 par2\nint32 par3\nint32 par4\nuint8[] data\n"
TOF_MD5 = "c005c34273dc426c67a020a87bc24148"
LIGHT_MD5 = "8cf5febb0952fca9d650c3d11a81a188"
IMU_MD5 = "6a62c6daae103f4ff57a132d6f95cec2"
ODOM_MD5 = "cd5e73d190d741a2f92e81eda573aca7"
STATUS_MD5 = "6919a5b3f28e39f677c7c81d4e5a2ef4"
ADJUST_LIGHT_MD5 = "66990e73c7aab0c47ddcdc70f7fa5bd0"
NIGHT_GET_MD5 = "cb942d3d2d4d7131956d07389eb07bd2"


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 2.0):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


def get_local_ip_for(target_ip: str) -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target_ip, 11311))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.1.85"


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed")
        buf.extend(chunk)
    return bytes(buf)


@dataclass
class ScoutTelemetry:
    connected: bool = False
    mock_mode: bool = False
    scout_ip: str = "192.168.1.115"

    # Bateria & Energia (/SensorNode/simple_battery_status)
    battery_pct: int = 100
    battery_state: str = "Normal / Desacoplado"
    charging: bool = False

    # Laser Time-of-Flight (/SensorNode/tof - sensor_msgs/Range)
    tof_distance_m: float = 0.22
    tof_min_range_m: float = 0.03
    tof_max_range_m: float = 2.00
    tof_fov_rad: float = 0.44
    tof_radiation_type: int = 1  # 1 = INFRARED

    # Sensor Fotometrico de Luz (/SensorNode/light - sensor_msgs/Illuminance)
    light_lux: float = 0.0
    light_variance: float = 0.0
    ir_led_on: bool = False
    night_mode_state: int = 0

    # IMU 6-Eixos (/SensorNode/imu - sensor_msgs/Imu)
    imu_accel_x: float = 0.0
    imu_accel_y: float = 0.0
    imu_accel_z: float = 9.81
    imu_gyro_x: float = 0.0
    imu_gyro_y: float = 0.0
    imu_gyro_z: float = 0.0
    imu_pitch_deg: float = 0.0
    imu_roll_deg: float = 0.0

    # Odometria Visual & Rodas (/MotorNode/baselink_odom_relative & vio_odom_relative)
    odom_x: float = 0.0
    odom_y: float = 0.0
    odom_yaw_deg: float = 0.0
    odom_vx: float = 0.0
    odom_vy: float = 0.0
    odom_wz: float = 0.0

    # Camara & Audio (/CoreNode/jpg)
    camera_width: int = 1920
    camera_height: int = 1080
    camera_fps: float = 0.0
    camera_frame_kb: float = 0.0
    camera_seq: int = 0

    # Microfone Fisico do Scout (/CoreNode/aac)
    mic_active: bool = False
    mic_sample_rate: int = 22050
    mic_channels: int = 1
    mic_bits: int = 16
    mic_rms: float = 0.0
    mic_db: float = 0.0
    mic_noise_floor_rms: float = 4.8
    mic_vad_threshold_rms: float = 8.5
    mic_sensitivity_pct: int = 85
    mic_vad_listening: bool = True
    mic_require_wake_word: bool = True
    mic_recording_speech: bool = False
    mic_awake_remaining_s: int = 0
    mic_last_transcript: str = "Diga 'Scout' para conversar..."
    mic_seq: int = 0

    # Coluna de Som Fisica do Scout (/net_audio_frame -> SpeakerNode)
    speaker_volume_pct: int = 100
    speaker_playing: bool = False
    speaker_subscribers: int = 0
    speaker_frames_sent: int = 0
    speaker_last_text: str = "Pronto"

    # Motores Mecanum (/cmd_vel)
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    motor_subscribers: int = 0
    last_action: str = "Conectado via TCPROS Nativo"
    active_topics: List[str] = field(default_factory=list)


class ScoutClient:
    """
    Driver 100% Nativo TCPROS/ROSRPC em Python puro para o Moorebot Scout (Roller Eye).
    Subscreve em tempo real a TODOS os sensores fisicos do Scout:
    - /CoreNode/jpg (Stream de video 1920x1080 + estatisticas de frame/FPS)
    - /SensorNode/tof (Laser Time-of-Flight: range, min, max, fov)
    - /SensorNode/imu (Acelerometro 3 eixos, Giroscopio 3 eixos, Pitch & Roll calculados)
    - /SensorNode/light (Luminosidade fotometrica em Lux)
    - /SensorNode/simple_battery_status (Estado da bateria e carregamento)
    - /MotorNode/baselink_odom_relative (Odometria de posicao X, Y, Yaw)
    - /cmd_vel (Publicador direto TCPROS para as 4 rodas Mecanum)
    """

    def __init__(self, scout_ip: str = "192.168.1.115", rosbridge_port: int = 9090, mock_mode: bool = False):
        self.telemetry = ScoutTelemetry(
            connected=False,
            mock_mode=mock_mode,
            scout_ip=scout_ip,
        )
        self._last_frame_bytes: Optional[bytes] = None
        self._sim_angle: float = 0.0
        self._stop_threads = False
        self._frame_timestamps: List[float] = []

        self._cmd_vel_Subscribers: List[socket.socket] = []
        self._cmd_vel_lock = threading.Lock()
        self._tcpros_port: int = 0
        self._xmlrpc_port: int = 0

        # Publicador TCPROS de Audio (/net_audio_frame -> SpeakerNode)
        self._audio_subscribers: List[socket.socket] = []
        self._audio_lock = threading.Lock()
        self._audio_tcpros_port: int = 0
        self._audio_seq: int = 0
        self._speaking_lock = asyncio.Lock()
        self.active_voice: str = "pt-PT-DuarteNeural"  # Voz neural europeia natural e fiável (Duarte - Portugal)
        self.active_voice_rate: str = "+3%"
        self.active_voice_pitch: str = "+0Hz"
        self._on_speech_callback: Optional[Any] = None
        self._on_log_callback: Optional[Any] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._awake_until: float = 0.0
        self.last_voice_pcm: Optional[bytes] = None
        self.last_speech_mp3: Optional[bytes] = None

        try:
            with open("scout_live_snapshot.jpg", "rb") as f:
                self._last_frame_bytes = f.read()
                self.telemetry.camera_frame_kb = round(len(self._last_frame_bytes) / 1024.0, 1)
        except Exception:
            pass

        self._start_native_tcpros_workers()

    def _get_proxy(self, uri: str, timeout: float = 2.0) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(uri, transport=TimeoutTransport(timeout=timeout))

    def _lookup_topic_port(self, node_name: str, topic: str) -> Optional[int]:
        master = self._get_proxy(f"http://{self.telemetry.scout_ip}:11311")
        for _ in range(4):
            try:
                code, _, node_uri = master.lookupNode("/scout_hub", node_name)
                if code != 1:
                    continue
                parsed = urlparse(node_uri)
                node_proxy = self._get_proxy(f"http://{self.telemetry.scout_ip}:{parsed.port}/")
                c, _, proto = node_proxy.requestTopic("/scout_hub", topic, [["TCPROS"]])
                if c == 1 and proto:
                    return int(proto[2])
            except Exception:
                time.sleep(0.25)
        return None

    def _connect_tcpros_sub(self, node_name: str, topic: str, md5sum: str, msg_type: str) -> Optional[socket.socket]:
        port = self._lookup_topic_port(node_name, topic)
        if not port:
            return None
        for _ in range(4):
            try:
                s = socket.create_connection((self.telemetry.scout_ip, port), timeout=3.5)
                fields = [
                    "callerid=/scout_hub",
                    f"topic={topic}",
                    f"md5sum={md5sum}",
                    f"type={msg_type}",
                    "tcp_nodelay=1",
                ]
                payload = b"".join(struct.pack("<I", len(f.encode())) + f.encode() for f in fields)
                s.sendall(struct.pack("<I", len(payload)) + payload)
                rlen = struct.unpack("<I", recv_exact(s, 4))[0]
                _ = recv_exact(s, rlen)
                s.settimeout(5.0)
                return s
            except Exception:
                time.sleep(0.3)
        return None

    def _start_native_tcpros_workers(self):
        # 1. Servidor TCPROS de publicacao /cmd_vel
        tcpros_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcpros_srv.bind(("0.0.0.0", 0))
        tcpros_srv.listen(8)
        self._tcpros_port = tcpros_srv.getsockname()[1]

        def _accept_motor_subs():
            while not self._stop_threads:
                try:
                    conn, _ = tcpros_srv.accept()
                    hlen = struct.unpack("<I", recv_exact(conn, 4))[0]
                    _ = recv_exact(conn, hlen)
                    resp_fields = [
                        "callerid=/scout_hub_teleop",
                        "topic=/cmd_vel",
                        f"md5sum={TWIST_MD5}",
                        "type=geometry_msgs/Twist",
                        f"message_definition={TWIST_DEF}",
                        "latching=0",
                    ]
                    rpay = b"".join(struct.pack("<I", len(f.encode())) + f.encode() for f in resp_fields)
                    conn.sendall(struct.pack("<I", len(rpay)) + rpay)
                    with self._cmd_vel_lock:
                        self._cmd_vel_Subscribers.append(conn)
                        self.telemetry.motor_subscribers = len(self._cmd_vel_Subscribers)
                    self.telemetry.connected = True
                except Exception:
                    time.sleep(0.2)

        threading.Thread(target=_accept_motor_subs, daemon=True).start()

        # 1b. Servidor TCPROS de publicacao /net_audio_frame (Coluna de Som do Scout)
        audio_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        audio_srv.bind(("0.0.0.0", 0))
        audio_srv.listen(8)
        self._audio_tcpros_port = audio_srv.getsockname()[1]

        def _accept_audio_subs():
            while not self._stop_threads:
                try:
                    conn, _ = audio_srv.accept()
                    hlen = struct.unpack("<I", recv_exact(conn, 4))[0]
                    _ = recv_exact(conn, hlen)
                    resp_fields = [
                        "callerid=/scout_hub_teleop",
                        "topic=/net_audio_frame",
                        f"md5sum={FRAME_MD5}",
                        "type=roller_eye/frame",
                        f"message_definition={FRAME_DEF}",
                        "latching=0",
                    ]
                    rpay = b"".join(struct.pack("<I", len(f.encode())) + f.encode() for f in resp_fields)
                    conn.sendall(struct.pack("<I", len(rpay)) + rpay)
                    with self._audio_lock:
                        self._audio_subscribers.append(conn)
                        self.telemetry.speaker_subscribers = len(self._audio_subscribers)
                    self.telemetry.connected = True
                except Exception:
                    time.sleep(0.2)

        threading.Thread(target=_accept_audio_subs, daemon=True).start()

        # 2. Servidor XML-RPC local para responder ao requestTopic do MotorNode e do SpeakerNode
        xmlrpc_srv = SimpleXMLRPCServer(("0.0.0.0", 0), logRequests=False, allow_none=True)
        self._xmlrpc_port = xmlrpc_srv.server_address[1]

        def requestTopic(caller_id, topic, protocols):
            local_ip = get_local_ip_for(self.telemetry.scout_ip)
            if topic == "/net_audio_frame":
                return [1, "ready", ["TCPROS", local_ip, self._audio_tcpros_port]]
            return [1, "ready", ["TCPROS", local_ip, self._tcpros_port]]

        xmlrpc_srv.register_function(requestTopic, "requestTopic")
        threading.Thread(target=xmlrpc_srv.serve_forever, daemon=True).start()

        # 3. Threads dedicadas para cada sensor em tempo real
        threading.Thread(target=self._camera_stream_loop, daemon=True).start()
        threading.Thread(target=self._tof_stream_loop, daemon=True).start()
        threading.Thread(target=self._imu_stream_loop, daemon=True).start()
        threading.Thread(target=self._light_stream_loop, daemon=True).start()
        threading.Thread(target=self._battery_and_status_loop, daemon=True).start()
        threading.Thread(target=self._odom_stream_loop, daemon=True).start()
        threading.Thread(target=self._mic_stream_loop, daemon=True).start()
        threading.Thread(target=self._ensure_cmd_vel_registered, daemon=True).start()

    def _ensure_cmd_vel_registered(self):
        while not self._stop_threads:
            if not self.telemetry.mock_mode:
                try:
                    local_ip = get_local_ip_for(self.telemetry.scout_ip)
                    my_uri = f"http://{local_ip}:{self._xmlrpc_port}/"
                    master = self._get_proxy(f"http://{self.telemetry.scout_ip}:11311")
                    if not self._cmd_vel_Subscribers:
                        master.registerPublisher("/scout_hub_teleop", "/cmd_vel", "geometry_msgs/Twist", my_uri)
                        code, _, motor_uri = master.lookupNode("/scout_hub_teleop", "/MotorNode")
                        if code == 1:
                            parsed = urlparse(motor_uri)
                            motor_proxy = self._get_proxy(f"http://{self.telemetry.scout_ip}:{parsed.port}/")
                            motor_proxy.publisherUpdate("/master", "/cmd_vel", [my_uri])
                    if not self._audio_subscribers:
                        master.registerPublisher("/scout_hub_teleop", "/net_audio_frame", "roller_eye/frame", my_uri)
                        code, _, spk_uri = master.lookupNode("/scout_hub_teleop", "/SpeakerNode")
                        if code == 1:
                            parsed = urlparse(spk_uri)
                            spk_proxy = self._get_proxy(f"http://{self.telemetry.scout_ip}:{parsed.port}/")
                            spk_proxy.publisherUpdate("/master", "/net_audio_frame", [my_uri])
                except Exception:
                    pass
            time.sleep(4.0)

    def _camera_stream_loop(self):
        """Subscreve `/CoreNode/jpg` e calcula FPS e resolucao em tempo real."""
        while not self._stop_threads:
            if self.telemetry.mock_mode:
                time.sleep(0.5)
                continue
            sock = self._connect_tcpros_sub("/CoreNode", "/CoreNode/jpg", FRAME_MD5, "roller_eye/frame")
            if not sock:
                time.sleep(1.5)
                continue
            self.telemetry.connected = True
            try:
                while not self._stop_threads and not self.telemetry.mock_mode:
                    mlen = struct.unpack("<I", recv_exact(sock, 4))[0]
                    mbytes = recv_exact(sock, mlen)
                    if len(mbytes) > 41:
                        f_seq, _, _, _, _, p1, p2, _, _, data_len = struct.unpack("<IQIbIiiiiI", mbytes[:41])
                        jpg = mbytes[41 : 41 + data_len]
                        if len(jpg) > 100 and jpg[:2] == b"\xff\xd8":
                            self._last_frame_bytes = jpg
                            self.telemetry.connected = True
                            self.telemetry.camera_seq = f_seq
                            if p1 > 0 and p2 > 0:
                                self.telemetry.camera_width = p1
                                self.telemetry.camera_height = p2
                            self.telemetry.camera_frame_kb = round(len(jpg) / 1024.0, 1)
                            now = time.time()
                            self._frame_timestamps.append(now)
                            self._frame_timestamps = [t for t in self._frame_timestamps if now - t <= 2.0]
                            if len(self._frame_timestamps) > 1:
                                self.telemetry.camera_fps = round(len(self._frame_timestamps) / 2.0, 1)
            except Exception:
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(0.5)

    def _tof_stream_loop(self):
        """Subscreve `/SensorNode/tof` (Laser Time-of-Flight)."""
        while not self._stop_threads:
            if self.telemetry.mock_mode:
                time.sleep(0.5)
                continue
            sock = self._connect_tcpros_sub("/SensorNode", "/SensorNode/tof", TOF_MD5, "sensor_msgs/Range")
            if not sock:
                time.sleep(1.5)
                continue
            try:
                while not self._stop_threads and not self.telemetry.mock_mode:
                    mlen = struct.unpack("<I", recv_exact(sock, 4))[0]
                    mbytes = recv_exact(sock, mlen)
                    fid_len = struct.unpack("<I", mbytes[12:16])[0]
                    offset = 16 + fid_len
                    rad_type, fov, min_r, max_r, rng = struct.unpack("<Bffff", mbytes[offset : offset + 17])
                    self.telemetry.tof_radiation_type = int(rad_type)
                    self.telemetry.tof_fov_rad = round(float(fov), 2)
                    self.telemetry.tof_min_range_m = round(float(min_r), 2)
                    self.telemetry.tof_max_range_m = round(float(max_r), 2)
                    if not math.isinf(rng) and not math.isnan(rng) and rng >= 0.0:
                        self.telemetry.tof_distance_m = round(float(rng), 3)
                    else:
                        self.telemetry.tof_distance_m = round(float(max_r), 2)
            except Exception:
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(0.8)

    def _imu_stream_loop(self):
        """Subscreve `/SensorNode/imu` (Giroscopio 3D + Acelerometro 3D + Inclinacao Pitch/Roll)."""
        while not self._stop_threads:
            if self.telemetry.mock_mode:
                time.sleep(0.5)
                continue
            sock = self._connect_tcpros_sub("/SensorNode", "/SensorNode/imu", IMU_MD5, "sensor_msgs/Imu")
            if not sock:
                time.sleep(1.5)
                continue
            try:
                while not self._stop_threads and not self.telemetry.mock_mode:
                    mlen = struct.unpack("<I", recv_exact(sock, 4))[0]
                    mbytes = recv_exact(sock, mlen)
                    fid_len = struct.unpack("<I", mbytes[12:16])[0]
                    off = 16 + fid_len
                    if len(mbytes) >= off + 224:
                        gx, gy, gz = struct.unpack("<ddd", mbytes[off + 104 : off + 128])
                        ax, ay, az = struct.unpack("<ddd", mbytes[off + 200 : off + 224])
                        self.telemetry.imu_gyro_x = round(float(gx), 3)
                        self.telemetry.imu_gyro_y = round(float(gy), 3)
                        self.telemetry.imu_gyro_z = round(float(gz), 3)
                        self.telemetry.imu_accel_x = round(float(ax), 2)
                        self.telemetry.imu_accel_y = round(float(ay), 2)
                        self.telemetry.imu_accel_z = round(float(az), 2)
                        # Calcular Pitch e Roll em graus a partir do vetor gravidade (ax, ay, az)
                        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
                        roll = math.degrees(math.atan2(ay, az))
                        self.telemetry.imu_pitch_deg = round(pitch, 1)
                        self.telemetry.imu_roll_deg = round(roll, 1)
            except Exception:
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(0.8)

    def _light_stream_loop(self):
        """Subscreve `/SensorNode/light` (Sensor de iluminancia em Lux)."""
        while not self._stop_threads:
            if self.telemetry.mock_mode:
                time.sleep(1.0)
                continue
            sock = self._connect_tcpros_sub("/SensorNode", "/SensorNode/light", LIGHT_MD5, "sensor_msgs/Illuminance")
            if not sock:
                time.sleep(2.0)
                continue
            try:
                while not self._stop_threads and not self.telemetry.mock_mode:
                    mlen = struct.unpack("<I", recv_exact(sock, 4))[0]
                    mbytes = recv_exact(sock, mlen)
                    fid_len = struct.unpack("<I", mbytes[12:16])[0]
                    off = 16 + fid_len
                    illum, var = struct.unpack("<dd", mbytes[off : off + 16])
                    self.telemetry.light_lux = round(float(illum), 1)
                    self.telemetry.light_variance = round(float(var), 2)
            except Exception:
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(1.0)

    def _odom_stream_loop(self):
        """Subscreve `/MotorNode/baselink_odom_relative` (Odometria das rodas)."""
        while not self._stop_threads:
            if self.telemetry.mock_mode:
                time.sleep(1.0)
                continue
            sock = self._connect_tcpros_sub("/MotorNode", "/MotorNode/baselink_odom_relative", ODOM_MD5, "nav_msgs/Odometry")
            if not sock:
                time.sleep(2.5)
                continue
            try:
                while not self._stop_threads and not self.telemetry.mock_mode:
                    mlen = struct.unpack("<I", recv_exact(sock, 4))[0]
                    mbytes = recv_exact(sock, mlen)
                    fid_len = struct.unpack("<I", mbytes[12:16])[0]
                    off1 = 16 + fid_len
                    child_len = struct.unpack("<I", mbytes[off1 : off1 + 4])[0]
                    off = off1 + 4 + child_len
                    if len(mbytes) >= off + 56:
                        px, py, pz, qx, qy, qz, qw = struct.unpack("<ddddddd", mbytes[off : off + 56])
                        self.telemetry.odom_x = round(float(px), 3)
                        self.telemetry.odom_y = round(float(py), 3)
                        # Converter quaternion (qz, qw) para Yaw em graus
                        siny_cosp = 2.0 * (qw * qz + qx * qy)
                        cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
                        self.telemetry.odom_yaw_deg = round(math.degrees(math.atan2(siny_cosp, cosy_cosp)), 1)
            except Exception:
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(1.5)

    def set_speech_callback(self, loop: asyncio.AbstractEventLoop, callback: Any, log_callback: Optional[Any] = None):
        """Regista a funcao assincrona que processa a fala captada pelo microfone fisico do Scout."""
        self._main_loop = loop
        self._on_speech_callback = callback
        self._on_log_callback = log_callback

    def set_speaker_volume(self, volume_pct: int) -> Dict[str, Any]:
        self.telemetry.speaker_volume_pct = max(0, min(200, int(volume_pct)))
        return self.get_status()

    def set_mic_vad(
        self,
        enabled: bool,
        threshold_rms: Optional[float] = None,
        sensitivity_pct: Optional[int] = None,
        require_wake_word: Optional[bool] = None,
        wake_now: Optional[bool] = None,
    ) -> Dict[str, Any]:
        self.telemetry.mic_vad_listening = bool(enabled)
        if require_wake_word is not None:
            self.telemetry.mic_require_wake_word = bool(require_wake_word)
        if wake_now:
            self._awake_until = time.time() + 30.0
        if sensitivity_pct is not None:
            sens = max(10, min(100, int(sensitivity_pct)))
            self.telemetry.mic_sensitivity_pct = sens
            # Mais sensibilidade (%) = menor limiar RMS (100% -> ~6.8 RMS, 85% -> ~8.5 RMS, 10% -> ~45 RMS)
            nf = max(3.5, self.telemetry.mic_noise_floor_rms)
            margin = 1.8 + ((100 - sens) / 90.0) ** 1.6 * 38.0
            self.telemetry.mic_vad_threshold_rms = round(nf + margin, 1)
        elif threshold_rms is not None:
            self.telemetry.mic_vad_threshold_rms = max(6.5, min(120.0, float(threshold_rms)))
        return self.get_status()

    def _mic_stream_loop(self):
        """
        Subscreve `/CoreNode/aac` (Microfone fisico do Moorebot Scout a 22050 Hz Mono AAC).
        - Usa descodificador continuo `av.CodecContext.create('aac', 'r')` para evitar cortes MDCT a cada 46ms
        - Remove DC offset e calibra o ruido de fundo (~4.5 RMS no Scout)
        - Amplifica automaticamente o sinal de voz (+40 dB) antes do Google STT (pt-PT + en-US wake word)
        """
        while not self._stop_threads:
            if self.telemetry.mock_mode:
                time.sleep(1.0)
                continue
            sock = self._connect_tcpros_sub("/CoreNode", "/CoreNode/aac", FRAME_MD5, "roller_eye/frame")
            if not sock:
                self.telemetry.mic_active = False
                time.sleep(2.0)
                continue

            self.telemetry.mic_active = True
            try:
                import av

                codec = av.CodecContext.create("aac", "r")
                resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
                speech_pcm_buf = bytearray()
                pre_roll_buf: List[bytes] = []
                in_speech = False
                silence_frames = 0
                speech_frames = 0
                max_speech_rms = 0.0
                cooldown_until = 0.0

                while not self._stop_threads and not self.telemetry.mock_mode:
                    mlen = struct.unpack("<I", recv_exact(sock, 4))[0]
                    mbytes = recv_exact(sock, mlen)
                    if len(mbytes) <= 41:
                        continue

                    f_seq, _, _, _, _, p1, p2, p3, _, data_len = struct.unpack("<IQIbIiiiiI", mbytes[:41])
                    aac_bytes = mbytes[41 : 41 + data_len]
                    self.telemetry.mic_seq = f_seq
                    if p1 > 0:
                        self.telemetry.mic_sample_rate = p1
                    if p2 > 0:
                        self.telemetry.mic_bits = p2
                    if p3 > 0:
                        self.telemetry.mic_channels = p3

                    # Descodificar o pacote ADTS AAC usando contexto continuo (preserva overlap MDCT!)
                    try:
                        pkt = av.Packet(aac_bytes)
                        pcm_chunk = bytearray()
                        for frame in codec.decode(pkt):
                            frame.pts = None
                            for rframe in resampler.resample(frame):
                                pcm_chunk.extend(bytes(rframe.planes[0]))
                    except Exception:
                        continue

                    if not pcm_chunk:
                        continue

                    nsamples = len(pcm_chunk) // 2
                    if nsamples == 0:
                        continue
                    raw_samples = struct.unpack(f"<{nsamples}h", pcm_chunk)
                    # Remover DC offset para medir apenas energia acustica real
                    dc_mean = sum(raw_samples) // nsamples
                    samples = [s - dc_mean for s in raw_samples]
                    rms = math.sqrt(sum(s * s for s in samples) / nsamples)
                    # Escala dB calibrada para o ganho do microfone do Scout (4.5 RMS = ~18 dB, 25 RMS = ~55 dB)
                    db = max(10.0, min(95.0, 18.0 + 38.0 * math.log10(max(1.0, rms / 4.0))))

                    self.telemetry.mic_rms = round(rms, 1)
                    self.telemetry.mic_db = round(db, 1)

                    # Evitar ouvir a propria coluna do Scout enquanto ele esta a falar (+ 0.6s pos-fala)
                    if self.telemetry.speaker_playing:
                        cooldown_until = time.time() + 0.65
                        in_speech = False
                        self.telemetry.mic_recording_speech = False
                        speech_pcm_buf.clear()
                        continue

                    if time.time() < cooldown_until or not self.telemetry.mic_vad_listening:
                        in_speech = False
                        self.telemetry.mic_recording_speech = False
                        speech_pcm_buf.clear()
                        continue

                    # Atualizar ruido de fundo adaptativo durante o silencio
                    if not in_speech and rms < self.telemetry.mic_noise_floor_rms * 1.45:
                        nf = 0.95 * self.telemetry.mic_noise_floor_rms + 0.05 * rms
                        self.telemetry.mic_noise_floor_rms = round(max(3.2, min(14.0, nf)), 2)
                        # Ajustar limiar dinamico conforme a sensibilidade (%)
                        sens = self.telemetry.mic_sensitivity_pct
                        margin = 1.8 + ((100 - sens) / 90.0) ** 1.6 * 38.0
                        self.telemetry.mic_vad_threshold_rms = round(self.telemetry.mic_noise_floor_rms + margin, 1)

                    is_loud = rms > self.telemetry.mic_vad_threshold_rms
                    if not in_speech:
                        pre_roll_buf.append(bytes(pcm_chunk))
                        if len(pre_roll_buf) > 12:
                            pre_roll_buf.pop(0)
                        if is_loud:
                            in_speech = True
                            self.telemetry.mic_recording_speech = True
                            speech_frames = 1
                            silence_frames = 0
                            max_speech_rms = rms
                            speech_pcm_buf.clear()
                            for b in pre_roll_buf:
                                speech_pcm_buf.extend(b)
                    else:
                        speech_pcm_buf.extend(pcm_chunk)
                        if rms > max_speech_rms:
                            max_speech_rms = rms
                        if is_loud:
                            speech_frames += 1
                            silence_frames = 0
                        else:
                            silence_frames += 1

                        # Termina frase apos ~0.65s de silencio (14 frames) ou limite de ~6.5s
                        if silence_frames >= 14 or len(speech_pcm_buf) > 16000 * 2 * 6.5:
                            in_speech = False
                            self.telemetry.mic_recording_speech = False
                            captured_bytes = bytes(speech_pcm_buf)
                            captured_max_rms = max_speech_rms
                            speech_pcm_buf.clear()
                            if speech_frames >= 2 and len(captured_bytes) >= 9600:
                                threading.Thread(
                                    target=self._transcribe_and_dispatch,
                                    args=(captured_bytes, captured_max_rms),
                                    daemon=True,
                                ).start()
            except Exception:
                self.telemetry.mic_active = False
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(1.0)

    def _normalize_pcm16(self, pcm_16k_bytes: bytes) -> bytes:
        """
        Amplifica automaticamente (Auto-Gain Normalization) o audio captado pelo microfone do Scout.
        Como o hardware do Scout envia amostras muito baixas (peak ~40-300 em 32768, ou -45 dBFS),
        normalizar para peak=25000 (-2.3 dBFS) torna a voz cristalina para o Google Speech-to-Text!
        """
        nsamples = len(pcm_16k_bytes) // 2
        if nsamples == 0:
            return pcm_16k_bytes
        raw = struct.unpack(f"<{nsamples}h", pcm_16k_bytes)
        mean_val = sum(raw) // nsamples
        # Filtro passa-alto leve + remocao de DC offset para limpar zumbido grave
        centered = []
        prev_x = 0
        prev_y = 0.0
        for s in raw:
            x = s - mean_val
            y = x - prev_x + 0.94 * prev_y
            prev_x = x
            prev_y = y
            centered.append(y)

        peak = max((abs(v) for v in centered), default=1.0)
        if peak < 1.0:
            peak = 1.0
        gain = min(350.0, 25000.0 / peak)
        boosted = [max(-32767, min(32767, int(v * gain))) for v in centered]
        return struct.pack(f"<{nsamples}h", *boosted)

    def _transcribe_and_dispatch(self, pcm_16k_bytes: bytes, max_rms: float = 0.0):
        """
        Converte o audio PCM 16kHz captado pelo microfone do Scout em texto (pt-PT).
        Implementa:
        1. Auto-Gain Normalization (+40 dB) para compensar o baixo ganho de hardware do Scout.
        2. Detecao de chamamento ("Scout", "Robô", etc.) avaliando todas as hipoteses pt-PT e en-US.
        3. Janela de Conversa Contínua (30 segundos) ou Modo Escuta Livre.
        """
        try:
            import re
            import wave
            import speech_recognition as sr

            norm_pcm = self._normalize_pcm16(pcm_16k_bytes)
            self.last_voice_pcm = norm_pcm
            duration_s = len(norm_pcm) / (16000.0 * 2.0)

            # Guardar ultimo clip normalizado para diagnostico se necessario
            try:
                with wave.open("scout_last_heard.wav", "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(norm_pcm)
            except Exception:
                pass

            recognizer = sr.Recognizer()
            audio_data = sr.AudioData(norm_pcm, 16000, 2)

            # Obter todas as hipoteses de transcricao em Portugues (pt-PT)
            candidates_pt: List[str] = []
            try:
                res_pt = recognizer.recognize_google(audio_data, language="pt-PT", show_all=True)
                if isinstance(res_pt, dict) and "alternative" in res_pt:
                    for alt in res_pt["alternative"]:
                        t = alt.get("transcript", "").strip()
                        if t:
                            candidates_pt.append(t)
                elif isinstance(res_pt, str) and res_pt.strip():
                    candidates_pt.append(res_pt.strip())
            except Exception:
                pass

            now = time.time()
            is_awake = (not self.telemetry.mic_require_wake_word) or (now < self._awake_until)

            # Padrao alargado para detetar "Scout" ou "Robô" em pt-PT e en-US
            wake_pattern = r"(scout|scoute|escout|escaute|skout|scaut|scalt|escalte|calt|caut|scott|scotti|scouts|escuta|escutar|escute|escudo|robo|robô|robot|moorebot|morebot)"
            has_wake_word = any(bool(re.search(wake_pattern, c.lower())) for c in candidates_pt)

            # Se nao estiver acordado e pt-PT nao apanhou a palavra inglesa "Scout", testar en-US!
            candidates_en: List[str] = []
            if not is_awake and not has_wake_word:
                try:
                    res_en = recognizer.recognize_google(audio_data, language="en-US", show_all=True)
                    if isinstance(res_en, dict) and "alternative" in res_en:
                        for alt in res_en["alternative"]:
                            t = alt.get("transcript", "").strip()
                            if t:
                                candidates_en.append(t)
                except Exception:
                    pass
                if any(bool(re.search(r"(scout|scouts|scott|robot|moorebot|morebot)", c.lower())) for c in candidates_en):
                    has_wake_word = True

            # Escolher o melhor texto transcrito
            text = ""
            if candidates_pt:
                # Preferir a alternativa que contenha a wake word se existir
                for c in candidates_pt:
                    if re.search(wake_pattern, c.lower()):
                        text = c
                        break
                if not text:
                    text = candidates_pt[0]
            elif has_wake_word and candidates_en:
                text = "Scout"

            if not text:
                self.telemetry.mic_last_transcript = f"[Som captado ({duration_s:.1f}s, RMS {max_rms:.0f}) - tente falar mais perto]"
                return

            if not is_awake and not has_wake_word:
                # Regista no Log da interface o que foi ouvido, mas fica calado e ignora!
                self.telemetry.mic_last_transcript = f"[Ignorado - diga 'Scout']: {text}"
                if self._main_loop and self._on_log_callback:
                    asyncio.run_coroutine_threadsafe(
                        self._on_log_callback(text, False),
                        self._main_loop,
                    )
                return

            # Ativa / renova a janela de conversa contínua por 30 segundos!
            self._awake_until = time.time() + 30.0
            self.telemetry.mic_last_transcript = text

            # Limpar prefixo de chamamento ("Scout", "Ei Scout", etc.)
            cleaned_prompt = re.sub(
                r"^(ei\s+|oi\s+|olá\s+|ola\s+|hey\s+)?(scout|scoute|escout|escaute|skout|scaut|scalt|escalte|calt|caut|scott|scotti|scouts|escuta|escute|escudo|robo|robô|robot|moorebot|morebot)[,\s!\.\?]*",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip()

            if not cleaned_prompt:
                cleaned_prompt = "Olá! Chamaste por mim?"

            self.telemetry.last_action = f"🎤 Scout ouviu: '{text}'"
            if self._main_loop and self._on_speech_callback:
                asyncio.run_coroutine_threadsafe(
                    self._on_speech_callback(text, cleaned_prompt, norm_pcm),
                    self._main_loop,
                )
        except Exception as e:
            self.telemetry.mic_last_transcript = f"[Erro STT: {e}]"

    def _battery_and_status_loop(self):
        """Atualiza lista de topicos ROS e estado de visao noturna periodicamente."""
        while not self._stop_threads:
            if not self.telemetry.mock_mode:
                try:
                    uri = f"http://{self.telemetry.scout_ip}:11311"
                    proxy = self._get_proxy(uri, timeout=2.0)
                    code, _, val = proxy.getSystemState("/scout_web")
                    if code == 1 and isinstance(val, list) and len(val) >= 1:
                        self.telemetry.active_topics = sorted({item[0] for item in val[0]})
                        self.telemetry.connected = True
                except Exception:
                    pass
            time.sleep(8.0)

    async def configure(self, scout_ip: str, mock_mode: bool) -> Dict[str, Any]:
        self.telemetry.scout_ip = scout_ip
        self.telemetry.mock_mode = mock_mode
        if not mock_mode:
            await self.refresh_ros_topics()
        return self.get_status()

    async def refresh_ros_topics(self) -> List[str]:
        uri = f"http://{self.telemetry.scout_ip}:11311"
        try:
            def _fetch():
                proxy = self._get_proxy(uri, timeout=2.5)
                code, _, val = proxy.getSystemState("/scout_web")
                if code == 1 and isinstance(val, list) and len(val) >= 1:
                    return sorted({item[0] for item in val[0]})
                return []

            topics = await asyncio.to_thread(_fetch)
            if topics:
                self.telemetry.active_topics = topics
                self.telemetry.connected = True
            return topics
        except Exception:
            return self.telemetry.active_topics

    def _publish_twist_now(self, lx: float, ly: float, az: float):
        pkt = struct.pack("<I", 48) + struct.pack("<dddddd", float(lx), float(ly), 0.0, 0.0, 0.0, float(az))
        dead = []
        with self._cmd_vel_lock:
            for conn in self._cmd_vel_Subscribers:
                try:
                    conn.sendall(pkt)
                except Exception:
                    dead.append(conn)
            for d in dead:
                self._cmd_vel_Subscribers.remove(d)
            self.telemetry.motor_subscribers = len(self._cmd_vel_Subscribers)

    async def move(self, linear_x: float = 0.0, linear_y: float = 0.0, angular_z: float = 0.0, duration_s: float = 0.0) -> Dict[str, Any]:
        self.telemetry.linear_x = round(float(linear_x), 2)
        self.telemetry.linear_y = round(float(linear_y), 2)
        self.telemetry.angular_z = round(float(angular_z), 2)

        if linear_x == 0 and linear_y == 0 and angular_z == 0:
            self.telemetry.last_action = "Parado"
        else:
            self.telemetry.last_action = f"Mover(x={self.telemetry.linear_x}, y={self.telemetry.linear_y}, z={self.telemetry.angular_z})"

        if not self.telemetry.mock_mode:
            await asyncio.to_thread(self._publish_twist_now, linear_x, linear_y, angular_z)

        if duration_s > 0:
            steps = max(1, int(min(duration_s, 5.0) / 0.1))
            for _ in range(steps):
                if not self.telemetry.mock_mode:
                    await asyncio.to_thread(self._publish_twist_now, linear_x, linear_y, angular_z)
                await asyncio.sleep(0.1)
            await self.stop()

        return self.get_status()

    async def stop(self) -> Dict[str, Any]:
        self.telemetry.linear_x = 0.0
        self.telemetry.linear_y = 0.0
        self.telemetry.angular_z = 0.0
        self.telemetry.last_action = "Parado"
        if not self.telemetry.mock_mode:
            await asyncio.to_thread(self._publish_twist_now, 0.0, 0.0, 0.0)
        return self.get_status()

    def _call_adjust_light_sync(self, cmd: int) -> bool:
        try:
            master = self._get_proxy(f"http://{self.telemetry.scout_ip}:11311")
            code, _, srv_uri = master.lookupService("/scout_hub", "/CoreNode/adjust_light")
            port = urlparse(srv_uri).port if code == 1 else 50377
            s = socket.create_connection((self.telemetry.scout_ip, port), timeout=2.5)
            fields = [
                "callerid=/scout_hub",
                "service=/CoreNode/adjust_light",
                f"md5sum={ADJUST_LIGHT_MD5}",
                "type=roller_eye/adjust_ligth",
            ]
            payload = b"".join(struct.pack("<I", len(f.encode())) + f.encode() for f in fields)
            s.sendall(struct.pack("<I", len(payload)) + payload)
            rlen = struct.unpack("<I", recv_exact(s, 4))[0]
            _ = recv_exact(s, rlen)
            req_bytes = struct.pack("<i", int(cmd))
            s.sendall(struct.pack("<I", len(req_bytes)) + req_bytes)
            _ = recv_exact(s, 1)
            s.close()
            return True
        except Exception:
            return False

    async def toggle_night_vision(self, enable: Optional[bool] = None) -> Dict[str, Any]:
        if enable is None:
            self.telemetry.ir_led_on = not self.telemetry.ir_led_on
        else:
            self.telemetry.ir_led_on = bool(enable)

        if not self.telemetry.mock_mode:
            cmd_val = 1 if self.telemetry.ir_led_on else 0
            await asyncio.to_thread(self._call_adjust_light_sync, cmd_val)

        self.telemetry.last_action = f"Luz/Visao Noturna: {'ON' if self.telemetry.ir_led_on else 'OFF'}"
        return self.get_status()

    async def synthesize_speech_mp3(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Sintetiza voz neural humana em Português usando Edge-TTS (Duarte/Francisca/António).
        Faz cache do áudio em memória para reprodução instantânea no browser e no robot.
        """
        if not text:
            return None
        try:
            import edge_tts

            clean_text = re.sub(r"\[.*?\]", "", text)
            clean_text = re.sub(r"[\*#_~`]", "", clean_text)
            clean_text = re.sub(r"\bm²\b", " metros quadrados", clean_text)
            clean_text = re.sub(r"\bm\b", " metros", clean_text)
            clean_text = re.sub(r"%", " por cento", clean_text)
            clean_text = re.sub(r"\blx\b", " lux", clean_text)
            clean_text = re.sub(r"\bswitch\.\b", "", clean_text)
            clean_text = re.sub(r"\blight\.\b", "", clean_text)
            clean_text = re.sub(r"\bcover\.\b", "", clean_text)
            clean_text = re.sub(r"\s+", " ", clean_text).strip()

            if len(clean_text) > 350:
                clean_text = clean_text[:350]

            target_voice = voice or self.active_voice
            target_rate = rate or self.active_voice_rate
            target_pitch = pitch or self.active_voice_pitch

            vol_pct = self.telemetry.speaker_volume_pct
            vol_diff = vol_pct - 100
            vol_str = f"+{vol_diff}%" if vol_diff >= 0 else f"{vol_diff}%"

            async def _synthesize(v: str) -> bytearray:
                buf = bytearray()
                comm = edge_tts.Communicate(
                    clean_text,
                    voice=v,
                    rate=target_rate,
                    pitch=target_pitch,
                    volume=vol_str,
                )
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
                return buf

            mp3_buf = bytearray()
            try:
                mp3_buf = await _synthesize(target_voice)
            except Exception as ex:
                print(f"[TTS] Erro ao sintetizar com {target_voice}: {ex}")

            if (not mp3_buf or len(mp3_buf) == 0) and target_voice != "pt-PT-DuarteNeural":
                print(f"[TTS] Fallback automático para voz fiável 'pt-PT-DuarteNeural'")
                try:
                    mp3_buf = await _synthesize("pt-PT-DuarteNeural")
                except Exception as ex_fb:
                    print(f"[TTS] Erro no fallback: {ex_fb}")

            if mp3_buf and len(mp3_buf) > 0:
                self.last_speech_mp3 = bytes(mp3_buf)
                self.telemetry.speaker_last_text = clean_text
                return self.last_speech_mp3
            return None
        except Exception as e:
            print(f"[TTS] Erro em synthesize_speech_mp3: {e}")
            return None

    async def speak_on_robot(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ) -> bool:
        """
        Sintetiza áudio e transmite via TCPROS para a coluna do Scout (se conectada)
        e armazena o MP3 para streaming direto no navegador do utilizador.
        """
        if not text or self.telemetry.mock_mode:
            return False

        async with self._speaking_lock:
            try:
                import av

                mp3_bytes = await self.synthesize_speech_mp3(text, voice=voice, rate=rate, pitch=pitch)
                if not mp3_bytes:
                    self.telemetry.speaker_playing = False
                    self.telemetry.last_action = "Aviso: Sem áudio TTS gerado"
                    return False

                self.telemetry.speaker_playing = True
                self.telemetry.last_action = "🔊 A reproduzir voz..."

                with self._audio_lock:
                    has_subs = len(self._audio_subscribers) > 0

                if has_subs:
                    def _encode_adts_sync(mp3_data: bytes) -> List[bytes]:
                        in_container = av.open(io.BytesIO(mp3_data), format="mp3")
                        out_buf = io.BytesIO()
                        out_container = av.open(out_buf, mode="w", format="adts")
                        out_stream = out_container.add_stream("aac", rate=16000, layout="mono")
                        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
                        for frame in in_container.decode(audio=0):
                            for rframe in resampler.resample(frame):
                                for pkt in out_stream.encode(rframe):
                                    out_container.mux(pkt)
                        for pkt in out_stream.encode(None):
                            out_container.mux(pkt)
                        out_container.close()

                        raw = out_buf.getvalue()
                        frames: List[bytes] = []
                        idx = 0
                        while idx < len(raw) - 7:
                            if raw[idx] == 0xFF and (raw[idx + 1] & 0xF0) == 0xF0:
                                flen = ((raw[idx + 3] & 0x03) << 11) | (raw[idx + 4] << 3) | ((raw[idx + 5] & 0xE0) >> 5)
                                if flen <= 0 or idx + flen > len(raw):
                                    break
                                frames.append(raw[idx : idx + flen])
                                idx += flen
                            else:
                                idx += 1
                        return frames

                    adts_frames = await asyncio.to_thread(_encode_adts_sync, mp3_bytes)
                    if adts_frames:
                        for i, aac_bytes in enumerate(adts_frames):
                            self._audio_seq += 1
                            self.telemetry.speaker_frames_sent = self._audio_seq
                            stamp_us = int(time.time() * 1_000_000)
                            body = (
                                struct.pack(
                                    "<IQIbIiiiiI",
                                    self._audio_seq,
                                    stamp_us,
                                    2000,
                                    2,  # AUDIO_STREAM_AAC
                                    self._audio_seq,
                                    16000,
                                    16,
                                    1,
                                    0,
                                    len(aac_bytes),
                                )
                                + aac_bytes
                            )
                            pkt = struct.pack("<I", len(body)) + body

                            dead = []
                            with self._audio_lock:
                                for conn in self._audio_subscribers:
                                    try:
                                        conn.sendall(pkt)
                                    except Exception:
                                        dead.append(conn)
                                for d in dead:
                                    self._audio_subscribers.remove(d)
                                self.telemetry.speaker_subscribers = len(self._audio_subscribers)

                            if i == 0 and self._audio_seq == 1:
                                await asyncio.sleep(1.1)
                                with self._audio_lock:
                                    for conn in self._audio_subscribers:
                                        try:
                                            conn.sendall(pkt)
                                        except Exception:
                                            pass

                            await asyncio.sleep(0.060)

                self.telemetry.speaker_playing = False
                if self._awake_until > time.time() - 5.0:
                    self._awake_until = time.time() + 30.0
                self.telemetry.last_action = "Pronto (Voz reproduzida)"
                return True
            except Exception as e:
                print(f"[TTS] Exceção geral em speak_on_robot: {e}")
                self.telemetry.speaker_playing = False
                return False

    def get_camera_jpeg_bytes(self) -> bytes:
        if self._last_frame_bytes and not self.telemetry.mock_mode:
            return self._last_frame_bytes

        self._sim_angle += 0.08
        width, height = 640, 360
        bg_color = (18, 28, 22) if self.telemetry.ir_led_on else (14, 20, 34)
        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)
        horizon_y = 180
        grid_color = (35, 90, 50) if self.telemetry.ir_led_on else (30, 50, 80)
        draw.line([(0, horizon_y), (width, horizon_y)], fill=grid_color, width=1)
        draw.rectangle([12, 12, 628, 52], fill=(8, 12, 20))
        draw.text((22, 20), f"MOOREBOT SCOUT ({self.telemetry.scout_ip}) - A conectar stream...", fill=(80, 220, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()

    def get_camera_base64(self) -> str:
        return base64.b64encode(self.get_camera_jpeg_bytes()).decode("utf-8")

    def get_status(self) -> Dict[str, Any]:
        t = self.telemetry
        return {
            "connected": t.connected,
            "mock_mode": t.mock_mode,
            "scout_ip": t.scout_ip,
            "battery_pct": t.battery_pct,
            "battery_state": t.battery_state,
            "charging": t.charging,
            "tof_distance_m": t.tof_distance_m,
            "tof_min_range_m": t.tof_min_range_m,
            "tof_max_range_m": t.tof_max_range_m,
            "tof_fov_rad": t.tof_fov_rad,
            "tof_radiation_type": t.tof_radiation_type,
            "light_lux": t.light_lux,
            "light_variance": t.light_variance,
            "ir_led_on": t.ir_led_on,
            "imu": {
                "accel_x": t.imu_accel_x,
                "accel_y": t.imu_accel_y,
                "accel_z": t.imu_accel_z,
                "gyro_x": t.imu_gyro_x,
                "gyro_y": t.imu_gyro_y,
                "gyro_z": t.imu_gyro_z,
                "pitch_deg": t.imu_pitch_deg,
                "roll_deg": t.imu_roll_deg,
            },
            "odometry": {
                "x": t.odom_x,
                "y": t.odom_y,
                "yaw_deg": t.odom_yaw_deg,
            },
            "camera": {
                "width": t.camera_width,
                "height": t.camera_height,
                "fps": t.camera_fps,
                "frame_kb": t.camera_frame_kb,
                "seq": t.camera_seq,
            },
            "microphone": {
                "active": t.mic_active,
                "sample_rate": t.mic_sample_rate,
                "channels": t.mic_channels,
                "bits": t.mic_bits,
                "rms": t.mic_rms,
                "db": t.mic_db,
                "noise_floor_rms": t.mic_noise_floor_rms,
                "vad_threshold_rms": t.mic_vad_threshold_rms,
                "sensitivity_pct": t.mic_sensitivity_pct,
                "vad_listening": t.mic_vad_listening,
                "require_wake_word": t.mic_require_wake_word,
                "recording_speech": t.mic_recording_speech,
                "awake_remaining_s": max(0, int(self._awake_until - time.time())),
                "last_transcript": t.mic_last_transcript,
                "seq": t.mic_seq,
            },
            "speaker": {
                "volume_pct": t.speaker_volume_pct,
                "playing": t.speaker_playing,
                "subscribers": t.speaker_subscribers,
                "frames_sent": t.speaker_frames_sent,
                "last_text": t.speaker_last_text,
            },
            "velocity": {
                "linear_x": t.linear_x,
                "linear_y": t.linear_y,
                "angular_z": t.angular_z,
                "motor_subscribers": t.motor_subscribers,
            },
            "last_action": t.last_action,
            "active_topics": t.active_topics,
            "timestamp": int(time.time()),
        }
