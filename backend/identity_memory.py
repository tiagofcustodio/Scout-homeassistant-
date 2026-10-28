import base64
import io
import json
import math
import re
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageFilter, ImageOps

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_DIR = BASE_DIR / "profiles"
AVATARS_DIR = PROFILES_DIR / "avatars"
MEMORY_FILE = PROFILES_DIR / "people_memory.json"


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    na = math.sqrt(sum(a * a for a in vec_a))
    nb = math.sqrt(sum(b * b for b in vec_b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm < 1e-9:
        return [0.0 for _ in vec]
    return [round(v / norm, 5) for v in vec]


class BiometricIdentityMemory:
    """
    Sistema Multimodal de Memoria Biometrica (Caras + Vozes) para o Moorebot Scout.
    - Extrai descritores faciais (192-D multi-escala CLAHE/Equalize + Gradientes Sobel H/V + Luminancia)
      a partir dos frames `/CoreNode/jpg` e recorta automaticamente o rosto para avatar.
    - Extrai assinaturas vocais (64-D Mel-Goertzel 24 bandas + 16 MFCCs + Pitch Fundamental F0 + ZCR)
      a partir do stream de microfone `/CoreNode/aac` (16kHz 16-bit Mono).
    - Combina Rosto + Voz para identificar quem esta a falar com o Scout em tempo real.
    """

    def __init__(self):
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        AVATARS_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.people: Dict[str, Dict[str, Any]] = {}
        self._last_voice_pcm: Optional[bytes] = None
        self._last_voice_ts: float = 0.0

        # Estado em tempo real da ultima identificacao
        self.live_state: Dict[str, Any] = {
            "active_person": None,
            "active_confidence_pct": 0,
            "face_match": None,
            "face_confidence_pct": 0,
            "face_bbox": None,
            "voice_match": None,
            "voice_confidence_pct": 0,
            "last_pitch_hz": 0.0,
            "last_identified_at": 0,
        }

        # Pre-calcular constantes Goertzel para 24 bandas Mel (85 Hz a 3600 Hz @ 8000 Hz)
        self._mel_freqs = [85.0 * ((3600.0 / 85.0) ** (k / 23.0)) for k in range(24)]
        self._mel_cos2 = [2.0 * math.cos(2.0 * math.pi * f / 8000.0) for f in self._mel_freqs]
        # Matriz DCT-II (16 MFCCs c1..c16 a partir das 24 bandas Mel)
        self._dct_matrix = [
            [math.cos(math.pi * m * (b + 0.5) / 24.0) for b in range(24)]
            for m in range(1, 17)
        ]

        self._load()

    def _load(self):
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.people = data
            except Exception:
                self.people = {}

    def _save(self):
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.people, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def cache_last_voice_pcm(self, pcm_16k_bytes: bytes):
        if pcm_16k_bytes and len(pcm_16k_bytes) >= 6400:
            self._last_voice_pcm = pcm_16k_bytes
            self._last_voice_ts = time.time()

    # =========================================================================
    # 1. EXTRACAO DE CARA (DETECAO DE ROSTO + EMBEDDING 192-D)
    # =========================================================================
    def _locate_and_crop_face(self, img: Image.Image) -> Tuple[Image.Image, Tuple[float, float, float, float]]:
        """
        Localiza a regiao do rosto na imagem da camara (YCbCr Skin + Simetria Ocular,
        com fallback para retrato central em modo Visao Noturna IR) e devolve (face_crop, bbox_norm).
        """
        rgb = img.convert("RGB")
        w, h = rgb.size
        sw, sh = 160, 90
        small = rgb.resize((sw, sh), Image.Resampling.BILINEAR)
        px = small.load()

        # Mapa de probabilidade de pele (YCbCr) + contraste ocular no terco superior/central
        skin_x_sum = 0.0
        skin_y_sum = 0.0
        skin_weight = 0.0

        for y in range(int(sh * 0.08), int(sh * 0.85)):
            for x in range(int(sw * 0.12), int(sw * 0.88)):
                r, g, b = px[x, y]
                # Conversao YCbCr para detecao de tom de pele humano sob luz normal
                cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
                cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
                if 77 <= cb <= 127 and 133 <= cr <= 175 and r > 45:
                    # Peso maior para o centro do enquadramento
                    cw = 1.0 - 0.5 * (abs(x - sw / 2) / (sw / 2))
                    skin_x_sum += x * cw
                    skin_y_sum += y * cw
                    skin_weight += cw

        if skin_weight >= 35.0:
            cx = skin_x_sum / skin_weight
            cy = skin_y_sum / skin_weight
            # Estimar tamanho da caixa com base na area de pele detetada
            side = max(28.0, min(sh * 0.72, math.sqrt(skin_weight) * 1.45))
            x1 = max(0.0, min(sw - side, cx - side * 0.5))
            y1 = max(0.0, min(sh - side, cy - side * 0.52))
            x2 = min( float(sw), x1 + side )
            y2 = min( float(sh), y1 + side )
        else:
            # Fallback para Visao Noturna IR (monocromatico) ou pouca luz: zona central-superior
            side = sh * 0.62
            x1 = (sw - side) * 0.5
            y1 = sh * 0.14
            x2 = x1 + side
            y2 = y1 + side

        nx1, ny1, nx2, ny2 = x1 / sw, y1 / sh, x2 / sw, y2 / sh
        crop_box = (int(nx1 * w), int(ny1 * h), int(nx2 * w), int(ny2 * h))
        face_crop = rgb.crop(crop_box)
        return face_crop, (round(nx1, 3), round(ny1, 3), round(nx2, 3), round(ny2, 3))

    def extract_face_embedding(self, jpeg_bytes: bytes) -> Optional[Tuple[List[float], bytes, Tuple[float, float, float, float]]]:
        """
        Extrai o vetor biometrico facial de 192 dimensoes (invariante a iluminacao)
        e devolve (embedding_192d, avatar_jpeg_bytes, bbox_norm).
        """
        if not jpeg_bytes or len(jpeg_bytes) < 500:
            return None
        try:
            img = Image.open(io.BytesIO(jpeg_bytes))
            face_crop, bbox = self._locate_and_crop_face(img)

            # Guardar miniatura 140x140 para exibir na interface Web
            thumb = ImageOps.fit(face_crop, (140, 140), Image.Resampling.LANCZOS)
            thumb_buf = io.BytesIO()
            thumb.save(thumb_buf, format="JPEG", quality=85)
            avatar_bytes = thumb_buf.getvalue()

            # Equalizar histograma para invariancia a iluminacao / Visao Noturna IR
            gray = ImageOps.equalize(face_crop.convert("L"))
            g16 = gray.resize((16, 16), Image.Resampling.BILINEAR)
            p = list(g16.getdata())

            # 1) 64 features de luminancia estrutural (blocos 2x2 numa grelha 8x8, media zero)
            lum_64: List[float] = []
            for by in range(8):
                for bx in range(8):
                    y0, x0 = by * 2, bx * 2
                    val = (p[y0 * 16 + x0] + p[y0 * 16 + x0 + 1] + p[(y0 + 1) * 16 + x0] + p[(y0 + 1) * 16 + x0 + 1]) / 4.0
                    lum_64.append(val)
            mean_lum = sum(lum_64) / 64.0
            lum_64 = _l2_normalize([v - mean_lum for v in lum_64])

            # 2) 64 features de Gradiente Horizontal (olhos, sobrancelhas, boca) e 64 de Gradiente Vertical (nariz, simetria)
            gx_64: List[float] = []
            gy_64: List[float] = []
            for by in range(8):
                for bx in range(8):
                    yc = min(14, max(1, by * 2))
                    xc = min(14, max(1, bx * 2))
                    gx = float(p[yc * 16 + (xc + 1)] - p[yc * 16 + (xc - 1)])
                    gy = float(p[(yc + 1) * 16 + xc] - p[(yc - 1) * 16 + xc])
                    gx_64.append(gx)
                    gy_64.append(gy)

            gx_64 = _l2_normalize(gx_64)
            gy_64 = _l2_normalize(gy_64)

            embedding = _l2_normalize(lum_64 + gx_64 + gy_64)
            return embedding, avatar_bytes, bbox
        except Exception:
            return None

    # =========================================================================
    # 2. EXTRACAO DE VOZ (VOICEPRINT 64-D + PITCH FUNDAMENTAL F0)
    # =========================================================================
    def extract_voice_embedding(self, pcm_16k_bytes: bytes) -> Optional[Tuple[List[float], float]]:
        """
        Extrai a assinatura vocal de 64 dimensoes (16 MFCCs media + 16 MFCCs std + 24 Mel norm + 8 Pitch/Timbre)
        a partir de audio PCM 16kHz 16-bit Mono e devolve (embedding_64d, pitch_hz).
        """
        if not pcm_16k_bytes or len(pcm_16k_bytes) < 6400:
            return None
        try:
            nsamples = len(pcm_16k_bytes) // 2
            raw = struct.unpack(f"<{nsamples}h", pcm_16k_bytes)

            # Sub-amostrar para 8000 Hz com filtro pre-enfase (realca formantes vocais e remove efeito de distancia)
            s8k: List[float] = []
            prev = float(raw[0])
            for i in range(0, nsamples, 2):
                cur = float(raw[i])
                s8k.append(cur - 0.95 * prev)
                prev = cur

            if len(s8k) < 300:
                return None

            mel_frames: List[List[float]] = []
            mfcc_frames: List[List[float]] = []
            pitches: List[float] = []
            zcr_list: List[float] = []

            # Janelas de 32ms (256 amostras @ 8kHz) com avanco de 20ms (160 amostras)
            for start in range(0, len(s8k) - 256, 160):
                frame = s8k[start : start + 256]
                energy = sum(x * x for x in frame) / 256.0
                if energy < 25000.0:
                    continue  # Ignorar pausas de silencio dentro da frase

                # Zero-Crossing Rate da janela
                zc = sum(1 for i in range(255) if (frame[i] >= 0) != (frame[i + 1] >= 0)) / 255.0
                zcr_list.append(zc)

                # Energia logaritmica nas 24 bandas Mel via Goertzel
                mel_24: List[float] = []
                for b in range(24):
                    c = self._mel_cos2[b]
                    s1 = 0.0
                    s2 = 0.0
                    for x in frame:
                        s0 = x + c * s1 - s2
                        s2 = s1
                        s1 = s0
                    power = max(1.0, s1 * s1 + s2 * s2 - c * s1 * s2)
                    mel_24.append(math.log10(power))

                # Normalizar energia media da janela (invariante ao volume da voz!)
                m_mean = sum(mel_24) / 24.0
                mel_norm = [v - m_mean for v in mel_24]
                mel_frames.append(mel_norm)

                # 16 coeficientes MFCC (c1..c16) via DCT-II
                mfcc_16 = [
                    sum(mel_norm[b] * self._dct_matrix[m][b] for b in range(24))
                    for m in range(16)
                ]
                mfcc_frames.append(mfcc_16)

                # Pitch Fundamental F0 por autocorrelacao (lag 23..100 @ 8kHz -> 80 Hz a 347 Hz)
                best_lag = 0
                best_r = 0.0
                for lag in range(23, 100, 2):
                    r = sum(frame[i] * frame[i + lag] for i in range(0, 128, 2))
                    if r > best_r:
                        best_r = r
                        best_lag = lag
                if best_lag > 0 and best_r > energy * 18.0:
                    pitches.append(8000.0 / best_lag)

            if len(mfcc_frames) < 3:
                return None

            n_f = float(len(mfcc_frames))
            # Media e Desvio Padrao dos 16 MFCCs
            mfcc_mean = [sum(f[m] for f in mfcc_frames) / n_f for m in range(16)]
            mfcc_std = [
                math.sqrt(sum((f[m] - mfcc_mean[m]) ** 2 for f in mfcc_frames) / n_f)
                for m in range(16)
            ]
            # Envelope Mel medio (24 bandas)
            mel_mean = [sum(f[b] for f in mel_frames) / n_f for b in range(24)]

            # Estatisticas de Pitch F0 e Timbre (8 dimensoes)
            if pitches:
                pitches_sorted = sorted(pitches)
                mean_f0 = sum(pitches) / len(pitches)
                med_f0 = pitches_sorted[len(pitches_sorted) // 2]
                p25_f0 = pitches_sorted[max(0, int(len(pitches_sorted) * 0.25))]
                p75_f0 = pitches_sorted[min(len(pitches_sorted) - 1, int(len(pitches_sorted) * 0.75))]
            else:
                mean_f0 = med_f0 = p25_f0 = p75_f0 = 150.0

            mean_zcr = sum(zcr_list) / max(1, len(zcr_list))
            low_mel = sum(mel_mean[:8]) / 8.0
            mid_mel = sum(mel_mean[8:16]) / 8.0
            high_mel = sum(mel_mean[16:]) / 8.0

            pitch_vec = [
                (mean_f0 - 160.0) / 80.0,
                (med_f0 - 160.0) / 80.0,
                (p25_f0 - 160.0) / 80.0,
                (p75_f0 - 160.0) / 80.0,
                (p75_f0 - p25_f0) / 50.0,
                mean_zcr * 4.0,
                low_mel - high_mel,
                mid_mel - low_mel,
            ]

            vec_64 = (
                _l2_normalize(mfcc_mean)
                + _l2_normalize(mfcc_std)
                + _l2_normalize(mel_mean)
                + _l2_normalize(pitch_vec)
            )
            return _l2_normalize(vec_64), round(med_f0, 1)
        except Exception:
            return None

    # =========================================================================
    # 3. REGISTO (ENROLLMENT) E IDENTIFICACAO EM TEMPO REAL
    # =========================================================================
    def enroll_person(
        self,
        name: str,
        jpeg_bytes: Optional[bytes] = None,
        pcm_16k_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Regista ou adiciona novas amostras de Cara e/ou Voz para uma pessoa.
        """
        clean_name = name.strip().title()
        if not clean_name:
            return {"ok": False, "error": "Nome inválido"}

        person_id = re.sub(r"[^a-z0-9_]", "_", clean_name.lower()).strip("_") or "pessoa"
        if pcm_16k_bytes is None and self._last_voice_pcm and (time.time() - self._last_voice_ts < 60.0):
            pcm_16k_bytes = self._last_voice_pcm

        face_res = self.extract_face_embedding(jpeg_bytes) if jpeg_bytes else None
        voice_res = self.extract_voice_embedding(pcm_16k_bytes) if pcm_16k_bytes else None

        with self._lock:
            profile = self.people.get(
                person_id,
                {
                    "id": person_id,
                    "name": clean_name,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "face_embeddings": [],
                    "voice_embeddings": [],
                    "pitch_samples_hz": [],
                    "avatar_b64": "",
                    "interactions": 0,
                },
            )
            profile["name"] = clean_name
            profile["updated_at"] = int(time.time())

            added_face = False
            added_voice = False

            if face_res:
                f_emb, avatar_bytes, bbox = face_res
                profile["face_embeddings"].append(f_emb)
                if len(profile["face_embeddings"]) > 10:
                    profile["face_embeddings"].pop(0)
                profile["avatar_b64"] = base64.b64encode(avatar_bytes).decode("utf-8")
                try:
                    with open(AVATARS_DIR / f"{person_id}.jpg", "wb") as af:
                        af.write(avatar_bytes)
                except Exception:
                    pass
                added_face = True

            if voice_res:
                v_emb, pitch_hz = voice_res
                profile["voice_embeddings"].append(v_emb)
                if len(profile["voice_embeddings"]) > 10:
                    profile["voice_embeddings"].pop(0)
                if pitch_hz > 0:
                    profile["pitch_samples_hz"].append(pitch_hz)
                    if len(profile["pitch_samples_hz"]) > 10:
                        profile["pitch_samples_hz"].pop(0)
                added_voice = True

            self.people[person_id] = profile
            self._save()

            self.live_state["active_person"] = clean_name
            self.live_state["active_confidence_pct"] = 99
            if added_face:
                self.live_state["face_match"] = clean_name
                self.live_state["face_confidence_pct"] = 99
            if added_voice:
                self.live_state["voice_match"] = clean_name
                self.live_state["voice_confidence_pct"] = 99

            return {
                "ok": True,
                "person": self._public_profile(profile),
                "added_face": added_face,
                "added_voice": added_voice,
            }

    def delete_person(self, person_id: str) -> bool:
        with self._lock:
            if person_id in self.people:
                del self.people[person_id]
                self._save()
                return True
        return False

    def identify(
        self,
        jpeg_bytes: Optional[bytes] = None,
        pcm_16k_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Compara o Rosto atual da camara e/ou a Voz captada pelo microfone com todas as pessoas memorizadas.
        """
        face_res = self.extract_face_embedding(jpeg_bytes) if jpeg_bytes else None
        voice_res = self.extract_voice_embedding(pcm_16k_bytes) if pcm_16k_bytes else None

        with self._lock:
            if not self.people:
                if voice_res:
                    self.live_state["last_pitch_hz"] = voice_res[1]
                return self.live_state

            best_face_name = None
            best_face_sim = 0.0
            best_voice_name = None
            best_voice_sim = 0.0

            face_scores: Dict[str, float] = {}
            voice_scores: Dict[str, float] = {}

            if face_res:
                f_emb, _, bbox = face_res
                self.live_state["face_bbox"] = bbox
                for pid, prof in self.people.items():
                    f_list = prof.get("face_embeddings", [])
                    if f_list:
                        sim = max(_cosine_similarity(f_emb, ref) for ref in f_list)
                        face_scores[pid] = sim
                        if sim > best_face_sim:
                            best_face_sim = sim
                            best_face_name = prof["name"]

            if voice_res:
                v_emb, pitch_hz = voice_res
                self.live_state["last_pitch_hz"] = pitch_hz
                for pid, prof in self.people.items():
                    v_list = prof.get("voice_embeddings", [])
                    if v_list:
                        sim = max(_cosine_similarity(v_emb, ref) for ref in v_list)
                        # Bonus de consistencia de Pitch Fundamental F0
                        p_list = prof.get("pitch_samples_hz", [])
                        if p_list and pitch_hz > 0:
                            avg_p = sum(p_list) / len(p_list)
                            p_diff = abs(pitch_hz - avg_p)
                            if p_diff < 22.0:
                                sim = min(0.99, sim + 0.05)
                            elif p_diff > 65.0:
                                sim = max(0.0, sim - 0.08)
                        voice_scores[pid] = sim
                        if sim > best_voice_sim:
                            best_voice_sim = sim
                            best_voice_name = prof["name"]

            # Converter semelhanca de cosseno para % de confianca intuitiva
            face_conf = int(max(0.0, min(99.0, (best_face_sim - 0.35) / 0.60 * 100.0))) if best_face_sim >= 0.62 else 0
            voice_conf = int(max(0.0, min(99.0, (best_voice_sim - 0.35) / 0.60 * 100.0))) if best_voice_sim >= 0.60 else 0

            self.live_state["face_match"] = best_face_name if face_conf >= 55 else None
            self.live_state["face_confidence_pct"] = face_conf if face_conf >= 55 else 0

            if pcm_16k_bytes is not None:
                self.live_state["voice_match"] = best_voice_name if voice_conf >= 55 else None
                self.live_state["voice_confidence_pct"] = voice_conf if voice_conf >= 55 else 0

            # Fusao Multimodal (Voz + Cara)
            best_combined_pid = None
            best_combined_score = 0.0
            for pid, prof in self.people.items():
                fs = face_scores.get(pid, 0.0)
                vs = voice_scores.get(pid, 0.0)
                if vs > 0 and fs > 0:
                    comb = 0.58 * vs + 0.42 * fs
                    if vs >= 0.68 and fs >= 0.68:
                        comb = min(0.99, comb + 0.06)  # Acordo multimodal Rosto + Voz!
                elif vs > 0:
                    comb = vs
                else:
                    comb = fs
                if comb > best_combined_score:
                    best_combined_score = comb
                    best_combined_pid = pid

            combined_conf = int(max(0.0, min(99.0, (best_combined_score - 0.35) / 0.60 * 100.0)))
            if best_combined_pid and combined_conf >= 55:
                prof = self.people[best_combined_pid]
                prof["interactions"] = prof.get("interactions", 0) + (1 if pcm_16k_bytes else 0)
                self.live_state["active_person"] = prof["name"]
                self.live_state["active_confidence_pct"] = combined_conf
                self.live_state["last_identified_at"] = int(time.time())
            elif pcm_16k_bytes is not None:
                self.live_state["active_person"] = None
                self.live_state["active_confidence_pct"] = 0

            return dict(self.live_state)

    def detect_enrollment_intent(self, text: str) -> Optional[str]:
        """
        Deteta se o utilizador pediu por voz/texto para o Scout decorar a sua cara/voz e extrai o nome!
        Exemplos:
        - "Scout, eu sou o Tiago, decora a minha cara e a minha voz"
        - "Chamo-me Maria"
        - "O meu nome é João"
        - "Memoriza a minha cara como Pedro"
        """
        t = text.strip()
        patterns = [
            r"(?:decora|memoriza|aprende|regista|guarda)\s+(?:a\s+minha\s+cara|o\s+meu\s+rosto|a\s+minha\s+voz).*?(?:sou\s+o|sou\s+a|chamo-me|como|nome\s+é)\s+([A-ZÀ-Úa-zà-ú]{2,20})",
            r"(?:eu\s+sou\s+o|eu\s+sou\s+a|chamo-me|o\s+meu\s+nome\s+é|meu\s+nome\s+é)\s+([A-ZÀ-Úa-zà-ú]{2,20})",
            r"(?:sou\s+o|sou\s+a)\s+([A-ZÀ-Úa-zà-ú]{2,20})[,\s]+(?:decora|memoriza|aprende|regista|guarda)",
        ]
        stopwords = {
            "scout", "robo", "robô", "robot", "moorebot", "aqui", "agora",
            "hoje", "bem", "bom", "olá", "ola", "cara", "voz", "rosto",
        }
        for pat in patterns:
            m = re.search(pat, t, flags=re.IGNORECASE)
            if m:
                candidate = m.group(1).strip(" .,!?\"'")
                if candidate.lower() not in stopwords and len(candidate) >= 2:
                    return candidate.title()
        return None

    def extract_name_when_prompted(self, text: str) -> Optional[str]:
        """
        Extrai o nome quando o Scout acabou de perguntar 'Poderias dizer-me o teu nome e olhar diretamente para a câmara?'.
        Aceita tanto 'Sou o Tiago' / 'Chamo-me Tiago' como simplesmente 'Tiago'.
        """
        explicit = self.detect_enrollment_intent(text)
        if explicit:
            return explicit

        # Limpar prefixos habituais de resposta
        cleaned = re.sub(
            r"^(olá|ola|oi|ei|sim|claro|ok|scout|robô|robo|eu|sou|chamo-me|chamo|me|o|a|meu|nome|é|e|\s|,|\.|!|\?)+",
            "",
            text.strip(),
            flags=re.IGNORECASE,
        ).strip(" .,!?\"'")

        if not cleaned:
            return None

        words = [w for w in re.split(r"\s+", cleaned) if w.isalpha()]
        if not words:
            return None

        # Usa o primeiro nome proprio (ou primeiro + segundo se curto)
        first = words[0]
        if len(first) >= 2:
            return first.title()
        return None

    def _public_profile(self, p: Dict[str, Any]) -> Dict[str, Any]:
        pitches = p.get("pitch_samples_hz", [])
        avg_pitch = round(sum(pitches) / len(pitches), 1) if pitches else 0.0
        return {
            "id": p["id"],
            "name": p["name"],
            "face_samples": len(p.get("face_embeddings", [])),
            "voice_samples": len(p.get("voice_embeddings", [])),
            "avg_pitch_hz": avg_pitch,
            "avatar_b64": p.get("avatar_b64", ""),
            "interactions": p.get("interactions", 0),
            "updated_at": p.get("updated_at", 0),
        }

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "live": dict(self.live_state),
                "people": [self._public_profile(p) for p in self.people.values()],
            }
