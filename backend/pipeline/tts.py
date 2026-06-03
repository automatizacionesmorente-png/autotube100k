import os
import re
import asyncio
from pathlib import Path
from ..database import add_cost_event, add_step

# Edge TTS — voces Microsoft Neural, nativas en español, GRATIS
# Selección por tono: todas son español nativo de alta calidad

VOICE_MAP = {
    "misterio":     "es-ES-AlvaroNeural",     # grave, lento, oscuro — perfecto para misterio
    "motivacional": "es-MX-JorgeNeural",      # energético, latinoamericano, dinámico
    "documental":   "es-ES-ElviraNeural",     # femenina, autoritativa, clara
    "drama":        "es-ES-AlvaroNeural",     # profundo, dramático
    "humor":        "es-MX-DaliaNeural",      # femenina, cálida, expresiva
    "neutro":       "es-MX-JorgeNeural",      # natural, profesional
}

RATE_MAP = {
    "misterio":     "-12%",   # más lento — tensión, gravedad
    "motivacional": "+10%",   # más rápido — energía
    "documental":   "-5%",    # ligeramente lento — autoridad
    "drama":        "-18%",   # muy lento — impacto emocional
    "humor":        "+5%",    # ligero — naturalidad
    "neutro":       "-5%",    # ligeramente lento — claridad
}


XTTS_VENV   = "/root/tts-venv/bin/python"
XTTS_SCRIPT = "/root/autotube100k/xtts_generate.py"
VOICES_DIR  = "/root/autotube100k/voices"

# Mapa tono → perfil de voz de referencia (generado con Edge TTS, clonado por XTTS v2)
XTTS_VOICE_MAP = {
    "misterio":     "misterio_masculino",
    "drama":        "drama_masculino",
    "motivacional": "motivacional_masculino",
    "documental":   "documental_femenino",
    "humor":        "humor_femenino",
    "neutro":       "neutro_profesional",
    # Tonos extra disponibles (asignar manualmente si creas nuevos canales):
    # "truecrime":    "truecrime_femenino",
    # "historia":     "historia_masculino",
    # "conspiracion": "conspiracion_masculino",
    # "ciencia":      "ciencia_femenino",
}


def generate_audio(job_id: str, script: str, tone: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Intentar XTTS v2 primero (calidad narrador profesional, gratis) ───────
    if _xtts_available():
        try:
            voice_profile = XTTS_VOICE_MAP.get(tone, "neutro_profesional")
            ref_wav = f"{VOICES_DIR}/{voice_profile}.wav"
            add_step(job_id, "tts", "running",
                     f"Generando voz con XTTS v2 · perfil '{voice_profile}' · gratis…")
            _xtts_generate(script, output_path, ref_wav)
            size_mb = output_path.stat().st_size / 1024 / 1024
            add_cost_event(job_id, "xtts_v2", len(script), 0, 0)
            add_step(job_id, "tts", "done",
                     f"Audio XTTS v2: {size_mb:.1f} MB · {voice_profile} · 0.00€", 0)
            return output_path
        except Exception as e:
            add_step(job_id, "tts", "running",
                     f"XTTS v2 falló ({str(e)[:60]}), usando Edge TTS…")

    # ── Fallback: Edge TTS (gratis, buena calidad) ────────────────────────────
    voice = VOICE_MAP.get(tone, "es-ES-AlvaroNeural")
    rate  = RATE_MAP.get(tone, "-5%")
    add_step(job_id, "tts", "running",
             f"Voz Edge TTS {voice.split('-')[2]} · español nativo · gratis…")
    asyncio.run(_edge_tts(script, voice, rate, output_path))
    size_mb = output_path.stat().st_size / 1024 / 1024
    add_cost_event(job_id, "edge_tts", len(script), 0, 0)
    add_step(job_id, "tts", "done",
             f"Audio: {size_mb:.1f} MB · {voice.split('-')[2]} · 0.00€", 0)
    return output_path


def _xtts_available() -> bool:
    import shutil
    from pathlib import Path as P
    return (shutil.which(XTTS_VENV) or P(XTTS_VENV).exists()) and P(XTTS_SCRIPT).exists()


def _xtts_generate(script: str, output_path: Path, ref_wav: str = None):
    """Genera audio con XTTS v2 dividiendo en chunks y concatenando con ffmpeg."""
    import subprocess, shutil

    chunks = _split_text(script, max_chars=1200)
    tmp_dir = output_path.parent / "_xtts_tmp"
    tmp_dir.mkdir(exist_ok=True)
    wav_files = []

    ref = ref_wav if ref_wav and Path(ref_wav).exists() else None

    try:
        for i, chunk in enumerate(chunks):
            txt = tmp_dir / f"chunk_{i}.txt"
            wav = tmp_dir / f"chunk_{i}.wav"
            txt.write_text(chunk, encoding="utf-8")

            cmd = [XTTS_VENV, XTTS_SCRIPT, str(txt), str(wav)]
            if ref:
                cmd.append(ref)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0 or not wav.exists():
                raise RuntimeError(f"XTTS chunk {i} falló: {result.stderr[-200:]}")
            wav_files.append(wav)

        # Convertir todos los WAV a MP3 y concatenar
        if len(wav_files) == 1:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_files[0]),
                 "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)],
                check=True, capture_output=True
            )
        else:
            # Concatenar con ffmpeg
            list_file = tmp_dir / "wavlist.txt"
            list_file.write_text("\n".join(f"file '{w.resolve()}'" for w in wav_files))
            concat_wav = tmp_dir / "concat.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(list_file), str(concat_wav)],
                check=True, capture_output=True
            )
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(concat_wav),
                 "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)],
                check=True, capture_output=True
            )
    finally:
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass


async def _edge_tts(script: str, voice: str, rate: str, output_path: Path):
    import edge_tts
    import subprocess

    chunks = _split_text(script, 4000)

    async def _save(chunk: str, tmp: Path):
        comm = edge_tts.Communicate(chunk, voice=voice, rate=rate)
        await comm.save(str(tmp))

    tmp_files = [output_path.parent / f"_tts_{i}.mp3" for i in range(len(chunks))]
    await asyncio.gather(*[_save(c, p) for c, p in zip(chunks, tmp_files)])

    if len(tmp_files) == 1:
        tmp_files[0].rename(output_path)
    else:
        lst = output_path.parent / "_tts_list.txt"
        lst.write_text("\n".join(f"file '{f.resolve()}'" for f in tmp_files))
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(lst), "-c", "copy", str(output_path)],
            check=True, capture_output=True
        )
        lst.unlink(missing_ok=True)
        for f in tmp_files:
            f.unlink(missing_ok=True)


def _split_text(text: str, max_chars: int = 4000) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text.replace("\n", " "))
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) + 1 <= max_chars:
            current += s + " "
        else:
            if current.strip():
                chunks.append(current.strip())
            current = s + " "
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]
