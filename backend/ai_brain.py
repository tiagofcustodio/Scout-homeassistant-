import asyncio
import json
import os
from typing import Any, Dict, List, Optional
import httpx

from backend.scout_client import ScoutClient

SYSTEM_PROMPT = """Tu és o Scout, um pequeno robô doméstico inteligente e companheiro (Moorebot Scout).
REGRAS DE CONVERSAÇÃO:
1. Fala sempre em Português de Portugal (pt-PT), de forma muito natural, fluida, humana e curta (1 a 2 frases no máximo, porque a tua resposta vai ser dita em voz alta pela tua coluna).
2. Tens memória biométrica de caras e vozes: se o contexto indicar quem está a falar contigo (ex: [PESSOA IDENTIFICADA: ...]), trata essa pessoa pelo nome! Se não souberes quem é e a pessoa perguntar, pede-lhe para dizer o nome para decorares a cara e a voz dela.
3. NUNCA menciones valores de sensores (distância ToF, graus de inclinação, luz, bateria, percentagens de confiança, etc.) a menos que o utilizador pergunte explicitamente por eles!
4. Se o utilizador apenas cumprimentar (ex: "Olá", "Tudo bem?"), responde como um amigo de forma simpática e natural, sem despejar dados técnicos."""

SCOUT_TOOLS_DECLARATIONS = [
    {
        "type": "function",
        "name": "move_robot",
        "description": "Move o robo Moorebot Scout usando as rodas Mecanum (frente/tras, strafe esquerda/direita ou rotacao).",
        "parameters": {
            "type": "object",
            "properties": {
                "linear_x": {
                    "type": "number",
                    "description": "Velocidade frente (+0.1 a +0.5) ou tras (-0.1 a -0.5).",
                },
                "linear_y": {
                    "type": "number",
                    "description": "Velocidade lateral Mecanum esquerda (+0.1 a +0.5) ou direita (-0.1 a -0.5).",
                },
                "angular_z": {
                    "type": "number",
                    "description": "Rotacao esquerda (+0.2 a +1.0) ou direita (-0.2 a -1.0).",
                },
                "duration_s": {
                    "type": "number",
                    "description": "Duracao do movimento em segundos (ex: 1.5).",
                },
            },
            "required": ["linear_x", "linear_y", "angular_z"],
        },
    },
    {
        "type": "function",
        "name": "stop_robot",
        "description": "Para imediatamente todos os motores do robo.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "toggle_night_vision",
        "description": "Liga ou desliga o LED infravermelho de visao noturna do Scout.",
        "parameters": {
            "type": "object",
            "properties": {
                "enable": {
                    "type": "boolean",
                    "description": "True para ligar a visao noturna, False para desligar.",
                }
            },
            "required": ["enable"],
        },
    },
]


class MultiProviderAIBrain:
    """
    Motor de IA intercambiavel em tempo real entre:
    1. Google Gemini (gemini-3.8-flash via SDK google-genai Interactions API)
    2. Ollama Local (ex: llama3.2-vision ou llava em http://localhost:11434)
    3. OpenAI (ex: gpt-4o-mini)
    """

    def __init__(self, scout: ScoutClient):
        self.scout = scout
        self.provider = os.getenv("AI_PROVIDER", "ollama").lower()
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._history: List[Dict[str, str]] = []

    def set_provider(self, provider: str, model: Optional[str] = None) -> Dict[str, str]:
        provider = provider.lower().strip()
        if provider not in ("gemini", "ollama", "openai"):
            raise ValueError("Provider invalido. Escolha: gemini, ollama ou openai.")
        self.provider = provider
        if model:
            if provider == "gemini":
                self.gemini_model = model
            elif provider == "ollama":
                self.ollama_model = model
            elif provider == "openai":
                self.openai_model = model
        return self.get_config()

    def get_config(self) -> Dict[str, str]:
        return {
            "provider": self.provider,
            "gemini_model": self.gemini_model,
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "openai_model": self.openai_model,
        }

    async def execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Executa no robo as funcoes chamadas pela IA (em background para nao bloquear a resposta)."""
        if name == "move_robot":
            asyncio.create_task(
                self.scout.move(
                    linear_x=float(args.get("linear_x", 0.0)),
                    linear_y=float(args.get("linear_y", 0.0)),
                    angular_z=float(args.get("angular_z", 0.0)),
                    duration_s=float(args.get("duration_s", 1.2)),
                )
            )
            return {"executed": "move_robot", "args": args}
        elif name == "stop_robot":
            await self.scout.stop()
            return {"executed": "stop_robot"}
        elif name == "toggle_night_vision":
            await self.scout.toggle_night_vision(bool(args.get("enable", True)))
            return {"executed": "toggle_night_vision", "enable": args.get("enable")}
        return {"executed": "unknown", "name": name}

    async def _apply_fast_intent_fallback(self, prompt: str) -> List[Dict[str, Any]]:
        """
        Garante que comandos de voz diretos (ex: do Smartwatch) acionam o robo
        mesmo quando o modelo local nao suporta tool-calling nativo.
        """
        p = prompt.lower()
        actions = []
        if any(w in p for w in ["para", "parar", "stop"]):
            actions.append(await self.execute_tool("stop_robot", {}))
        elif any(w in p for w in ["frente", "avanca", "avancar", "avança", "avançar", "forward"]):
            actions.append(await self.execute_tool("move_robot", {"linear_x": 0.3, "linear_y": 0.0, "angular_z": 0.0, "duration_s": 1.5}))
        elif any(w in p for w in ["tras", "trás", "recua", "recuar", "back"]):
            actions.append(await self.execute_tool("move_robot", {"linear_x": -0.3, "linear_y": 0.0, "angular_z": 0.0, "duration_s": 1.5}))
        elif any(w in p for w in ["roda esquerda", "vira esquerda", "gira esquerda", "esquerda"]):
            actions.append(await self.execute_tool("move_robot", {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.6, "duration_s": 1.2}))
        elif any(w in p for w in ["roda direita", "vira direita", "gira direita", "direita"]):
            actions.append(await self.execute_tool("move_robot", {"linear_x": 0.0, "linear_y": 0.0, "angular_z": -0.6, "duration_s": 1.2}))
        elif "visao noturna" in p or "visão noturna" in p or "infravermelho" in p or "luz" in p:
            enable = not ("desliga" in p or "apaga" in p)
            actions.append(await self.execute_tool("toggle_night_vision", {"enable": enable}))
        return actions

    async def ask(self, prompt: str, include_vision: bool = True, speaker_context: str = "") -> Dict[str, Any]:
        """Envia o pedido do utilizador para o provedor de IA selecionado."""
        image_b64 = self.scout.get_camera_base64() if include_vision else None

        # Só inclui os dados dos sensores no contexto se o utilizador perguntar por eles!
        p_low = prompt.lower()
        wants_sensors = any(
            k in p_low
            for k in [
                "distancia", "distância", "longe", "perto", "obstaculo", "obstáculo",
                "sensor", "tof", "bateria", "luz", "inclinacao", "inclinação",
                "graus", "estado", "telemetria", "metros", "cm",
            ]
        )
        status_context = f"{speaker_context}\n" if speaker_context else ""
        if wants_sensors:
            status_context += (
                f"[Dados dos sensores caso necessário: Distância à frente={self.scout.telemetry.tof_distance_m}m, "
                f"Inclinação Pitch={self.scout.telemetry.imu_pitch_deg}°, Roll={self.scout.telemetry.imu_roll_deg}°, "
                f"Visão Noturna={'Ligada' if self.scout.telemetry.ir_led_on else 'Desligada'}]\n"
            )

        if self.provider == "gemini":
            return await self._ask_gemini(prompt, status_context, image_b64)
        elif self.provider == "ollama":
            return await self._ask_ollama(prompt, status_context, image_b64)
        else:
            return await self._ask_openai(prompt, status_context, image_b64)

    async def _ask_gemini(self, prompt: str, status_context: str, image_b64: Optional[str]) -> Dict[str, Any]:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        executed_actions: List[Dict[str, Any]] = []

        if not api_key or api_key == "coloque_sua_chave_aqui":
            executed_actions = await self._apply_fast_intent_fallback(prompt)
            return {
                "provider": "gemini (sem chave)",
                "reply": (
                    f"Recebi o comando: '{prompt}'. {status_context}. "
                    "Configure uma chave Gemini API valida (começa por AIza...) no ficheiro .env!"
                ),
                "actions": executed_actions,
            }

        try:
            from google import genai

            contents: List[Dict[str, Any]] = [
                {"type": "text", "text": f"{status_context}\nUtilizador: {prompt}"}
            ]
            if image_b64:
                contents.append(
                    {
                        "type": "image",
                        "data": image_b64,
                        "mime_type": "image/jpeg",
                    }
                )

            def _call_gemini_sync():
                client = genai.Client(api_key=api_key, http_options={"timeout": 12000})
                return client.interactions.create(
                    model=self.gemini_model,
                    system_instruction=SYSTEM_PROMPT,
                    input=contents,
                    tools=SCOUT_TOOLS_DECLARATIONS,
                )

            # Executa num worker thread com timeout de 14s para NUNCA bloquear o loop do FastAPI!
            interaction = await asyncio.wait_for(
                asyncio.to_thread(_call_gemini_sync),
                timeout=14.0,
            )

            reply_parts: List[str] = []
            for step in interaction.steps:
                if step.type == "function_call":
                    args = step.arguments if isinstance(step.arguments, dict) else {}
                    res = await self.execute_tool(step.name, args)
                    executed_actions.append(res)
                elif hasattr(step, "content") and step.content:
                    for part in step.content:
                        if hasattr(part, "text") and part.text:
                            reply_parts.append(part.text)

            reply_text = " ".join(reply_parts).strip()
            if not reply_text and executed_actions:
                reply_text = f"Comando executado com sucesso: {executed_actions[0]['executed']}."

            return {
                "provider": f"gemini ({self.gemini_model})",
                "reply": reply_text or "Pronto!",
                "actions": executed_actions,
            }
        except Exception as e:
            executed_actions = await self._apply_fast_intent_fallback(prompt)
            p_low = prompt.lower().strip()
            if any(g in p_low for g in ["ola", "olá", "bom dia", "boa tarde", "boa noite", "oi"]):
                natural_reply = "Olá Tiago! Tudo bem contigo? Como posso ajudar-te?"
            elif executed_actions:
                natural_reply = "Já está, a caminho!"
            else:
                natural_reply = f"Estou a ouvir-te, Tiago!"

            return {
                "provider": "Scout Voz Local",
                "reply": natural_reply,
                "actions": executed_actions,
            }

    async def _ask_ollama(self, prompt: str, status_context: str, image_b64: Optional[str]) -> Dict[str, Any]:
        executed_actions = await self._apply_fast_intent_fallback(prompt)
        try:
            is_vision_model = any(v in self.ollama_model.lower() for v in ["vision", "llava", "moondream", "minicpm", "bakllava"])
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                *self._history[-8:],
            ]
            user_msg: Dict[str, Any] = {
                "role": "user",
                "content": f"{status_context}{prompt}",
            }
            if image_b64 and is_vision_model:
                user_msg["images"] = [image_b64]
            messages.append(user_msg)

            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.post(
                    f"{self.ollama_url.rstrip('/')}/api/chat",
                    json={
                        "model": self.ollama_model,
                        "messages": messages,
                        "stream": False,
                        "keep_alive": "30m",
                        "options": {
                            "num_ctx": 1024,
                            "num_predict": 75,
                            "temperature": 0.6,
                        },
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                reply = data.get("message", {}).get("content", "Sem resposta do Ollama.").strip()
                self._history.append({"role": "user", "content": prompt})
                self._history.append({"role": "assistant", "content": reply})
                if len(self._history) > 12:
                    self._history = self._history[-12:]
                return {
                    "provider": f"ollama ({self.ollama_model})",
                    "reply": reply,
                    "actions": executed_actions,
                }
        except Exception as e:
            return {
                "provider": f"ollama ({self.ollama_model} offline)",
                "reply": (
                    f"Não foi possível ligar ao Ollama em {self.ollama_url} ({e}). "
                    f"Comandos físicos diretos executados: {len(executed_actions)}."
                ),
                "actions": executed_actions,
            }

    async def _ask_openai(self, prompt: str, status_context: str, image_b64: Optional[str]) -> Dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY", "")
        executed_actions = await self._apply_fast_intent_fallback(prompt)
        if not api_key or api_key == "coloque_sua_chave_aqui":
            return {
                "provider": "openai (sem chave)",
                "reply": "Defina OPENAI_API_KEY no ficheiro .env ou mude o seletor na Web App para Gemini ou Ollama.",
                "actions": executed_actions,
            }
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, timeout=14.0)
            user_content: List[Dict[str, Any]] = [
                {"type": "text", "text": f"{status_context}\n{prompt}"}
            ]
            if image_b64:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    }
                )
            completion = await client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            return {
                "provider": f"openai ({self.openai_model})",
                "reply": completion.choices[0].message.content or "Pronto!",
                "actions": executed_actions,
            }
        except Exception as e:
            return {
                "provider": "openai (erro)",
                "reply": f"Erro na OpenAI API: {e}",
                "actions": executed_actions,
            }
