import os
import asyncio
from pathlib import Path
from ..database import add_cost_event, add_step

# Voces neurales españolas de Microsoft Edge TTS (gratis, misma calidad que Azure)
VOICE_MAP = {
    "misterio":     "es-ES-AlvaroNeural",    # voz masculina grave, perfecta para misterio
    "motivacional": "es-ES-AlvaroNeural",    # masculina energética
    "documental":   "es-ES-ElviraNeural",    # femenina clara, ideal documentales
    "drama":        "es-ES-AlvaroNeural",    # masculina dramática
    "humor":        "es-ES-ElviraNeural",    # femenina cercana
    "neutro":       "es-ES-AlvaroNeural",    # masculina neutra profesional
}

# Ajuste de velocidad por tono
SPEED_MAP = {
    "misterio":     "-5%",   # más lenta, más tensión
    "motivacional": "+10%",  # más rápida, más energía
    "documental":   "-3%",   # ligeramente lenta, más solemne
    "drama":        "-8%",   # pausada, más dramática
    "humor":        "+5%",   # algo rápida, más dinámica
    "neutro":       "+0%",   # normal
}


def generate_audio(job_id: str, script: str, tone: str, output_path: Path) -> Path:
    """Genera audio con Edge TTS (gratis, voces neurales Microsoft). Fallback: Kokoro."""
    add_step(job_id, "tts", "running", "Convirtiendo guión a voz con Edge TTS neural...")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    voice = VOICE_MAP.get(tone, "es-ES-AlvaroNeural")
    rate  = SPEED_MAP.get(tone, "+0%")

    try:
        asyncio.run(_edge_tts_generate(script, voice, rate, output_path))
        size_mb = output_path.stat().st_size / 1024 / 1024
        add_cost_event(job_id, "edge_tts", len(script), 0, 0)  # totalmente gratis
        add_step(job_id, "tts", "done",
                 f"Audio listo: {size_mb:.1f} MB ({voice.split('-')[2]})", 0)
        return output_path

    except Exception as e:
        add_step(job_id, "tts", "running", f"Edge TTS falló ({e}), probando Kokoro...")
        return _kokoro_fallback(job_id, script, tone, output_path)


async def _edge_tts_generate(script: str, voice: str, rate: str, output_path: Path):
    """Divide el guión en chunks y los concatena en un solo MP3."""
    import edge_tts

    chunks = _split_text(script, max_chars=4000)  # Edge TTS aguanta hasta ~5000 chars
    tmp_files = []

    for i, chunk in enumerate(chunks):
        tmp_path = output_path.parent / f"_tts_chunk_{i}.mp3"
        tts = edge_tts.Communicate(chunk, voice=voice, rate=rate)
        await tts.save(str(tmp_path))
        tmp_files.append(tmp_path)

    if len(tmp_files) == 1:
        tmp_files[0].rename(output_path)
    else:
        # Concatenar chunks con ffmpeg
        import subprocess
        list_file = output_path.parent / "_tts_list.txt"
        list_file.write_text("\n".join(f"file '{f.resolve()}'" for f in tmp_files))
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(output_path)
        ], check=True, capture_output=True)
        list_file.unlink()
        for f in tmp_files:
            f.unlink(missing_ok=True)


def _kokoro_fallback(job_id: str, script: str, tone: str, output_path: Path) -> Path:
    """Fallback local con Kokoro ONNX (lento en CPU pero gratuito)."""
    import numpy as np

    kokoro_voice = {
        "misterio": "em_alex", "motivacional": "em_santa",
        "documental": "af_heart", "drama": "em_alex",
        "humor": "af_sky", "neutro": "af_heart",
    }.get(tone, "af_heart")

    from kokoro_onnx import Kokoro
    import soundfile as sf

    kokoro = Kokoro("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")
    chunks = _split_text(script, max_chars=500)
    all_audio = []
    for chunk in chunks:
        samples, sr = kokoro.create(chunk, voice=kokoro_voice, speed=1.0, lang="es")
        all_audio.append(samples)

    sf.write(str(output_path), np.concatenate(all_audio), sr)
    add_cost_event(job_id, "kokoro_tts", len(script), 0, 0)
    add_step(job_id, "tts", "done", f"Audio Kokoro: {output_path.name}", 0)
    return output_path


def _split_text(text: str, max_chars: int = 4000) -> list[str]:
    """Divide en frases respetando puntos, sin cortar palabras."""
    sentences = text.replace("\n", " ").split(". ")
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) + 2 <= max_chars:
            current += s + ". "
        else:
            if current.strip():
                chunks.append(current.strip())
            current = s + ". "
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]
