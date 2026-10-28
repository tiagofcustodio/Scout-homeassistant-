import asyncio
import os
import edge_tts

VOICE = "pt-PT-DuarteNeural"

SOUNDS = {
    "power_on.mp3": "Sistemas iniciados. Scout pronto a operar.",
    "power_down.mp3": "A encerrar sistemas. Até breve.",
    "charging.mp3": "A carregar bateria.",
    "leave_home.mp3": "A sair da base de carregamento.",
    "battery_low.mp3": "Atenção: bateria fraca. A regressar à base.",
    "obstacle_detected.mp3": "Obstáculo detetado.",
    "got_new_instrction.mp3": "Novo comando recebido.",
    "alert.mp3": "Atenção! Intruso detetado na área!"
}

async def generate():
    output_dir = os.path.join(os.path.dirname(__file__), "generated_sounds")
    os.makedirs(output_dir, exist_ok=True)
    
    for filename, text in SOUNDS.items():
        filepath = os.path.join(output_dir, filename)
        print(f"Generating {filename}: '{text}'...")
        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(filepath)
        print(f"Saved {filepath} ({os.path.getsize(filepath)} bytes)")

if __name__ == "__main__":
    asyncio.run(generate())
