import os
import numpy as np
from pathlib import Path
from ..database import add_cost_event, add_step

VOICE_MAP = {
    "misterio": "em_alex",
    "motivacional": "em_santa",
    "documental": "af_heart",
    "drama": "em_alex",
    "humor": "af_sky",
    "neutro": "af_heart",
}

def generate_audio(job_id: str, script: str, tone: str, output_path: Path) -> Path:
    add_step(job_id, "tts", "running", "Convirtiendo guión a voz con Kokoro TTS")

    try:
        from kokoro_onnx import Kokoro
        import soundfile as sf

        voice = VOICE_MAP.get(tone, "af_heart")
        kokoro = Kokoro("kokoro-v0_19.onnx", "voices-v1.0.bin")

        # Dividir en fragmentos de 500 chars para evitar límites
        chunks = _split_text(script, max_chars=500)
        all_audio = []

        for chunk in chunks:
            samples, sample_rate = kokoro.create(chunk, voice=voice, speed=1.0, lang="es")
            all_audio.append(samples)

        audio_combined = np.concatenate(all_audio)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), audio_combined, sample_rate)

        add_cost_event(job_id, "kokoro_tts", len(script), 0, 0)  # gratis
        add_step(job_id, "tts", "done", f"Audio generado: {output_path.name}", 0)
        return output_path

    except ImportError:
        # Fallback: Azure TTS si Kokoro no está instalado
        return _azure_fallback(job_id, script, tone, output_path)

def _azure_fallback(job_id: str, script: str, tone: str, output_path: Path) -> Path:
    import httpx

    add_step(job_id, "tts", "running", "Kokoro no disponible — usando Azure TTS como fallback")

    azure_key = os.environ.get("AZURE_TTS_KEY")
    region = os.environ.get("AZURE_TTS_REGION", "eastus")

    if not azure_key:
        raise RuntimeError("Kokoro no instalado y no hay AZURE_TTS_KEY configurada")

    voice_azure = {
        "misterio": "es-ES-AlvaroNeural",
        "motivacional": "es-ES-AlvaroNeural",
        "documental": "es-ES-ElviraNeural",
        "neutro": "es-ES-ElviraNeural",
    }.get(tone, "es-ES-ElviraNeural")

    ssml = f"""<speak version='1.0' xml:lang='es-ES'>
        <voice name='{voice_azure}'>{script}</voice>
    </speak>"""

    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": azure_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3",
    }

    with httpx.Client(timeout=120) as client:
        resp = client.post(url, headers=headers, content=ssml.encode())
        resp.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(resp.content)

    chars = len(script)
    cost = chars * 16 / 1_000_000 * 0.92  # EUR
    add_cost_event(job_id, "azure_tts", chars, 16 / 1_000_000, cost)
    add_step(job_id, "tts", "done", f"Audio Azure TTS: {output_path.name}", cost)
    return output_path

def _split_text(text: str, max_chars: int = 500) -> list[str]:
    sentences = text.replace("\n", " ").split(". ")
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars:
            current += s + ". "
        else:
            if current:
                chunks.append(current.strip())
            current = s + ". "
    if current:
        chunks.append(current.strip())
    return chunks
