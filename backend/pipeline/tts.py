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
    raw_path = output_path.with_suffix(".raw.mp3")
    if _xtts_available():
        try:
            voice_profile = XTTS_VOICE_MAP.get(tone, "neutro_profesional")
            ref_wav = f"{VOICES_DIR}/{voice_profile}.wav"
            add_step(job_id, "tts", "running",
                     f"Generando voz con XTTS v2 · perfil '{voice_profile}' · gratis…")
            _xtts_generate(script, raw_path, ref_wav)
            add_cost_event(job_id, "xtts_v2", len(script), 0, 0)
            _postprocess_audio(raw_path, output_path, tone)
            raw_path.unlink(missing_ok=True)
            size_mb = output_path.stat().st_size / 1024 / 1024
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
    asyncio.run(_edge_tts(script, voice, rate, raw_path))
    _postprocess_audio(raw_path, output_path, tone)
    raw_path.unlink(missing_ok=True)
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
    """
    Genera audio con XTTS v2 en UNA sola llamada al subproceso.
    El script xtts_generate.py carga el modelo una vez, divide el guion
    completo en frases internamente y concatena. Mucho más rápido y fiable
    que recargar el modelo de 2GB por cada fragmento.
    """
    import subprocess

    tmp_dir = output_path.parent / "_xtts_tmp"
    tmp_dir.mkdir(exist_ok=True)
    txt = tmp_dir / "full_script.txt"
    wav = tmp_dir / "full.wav"
    txt.write_text(script, encoding="utf-8")

    ref = ref_wav if ref_wav and Path(ref_wav).exists() else None
    cmd = [XTTS_VENV, XTTS_SCRIPT, str(txt), str(wav)]
    if ref:
        cmd.append(ref)

    # Timeout generoso: 32 min de audio a RTF 2.2 ≈ 70 min + margen
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0 or not wav.exists():
        raise RuntimeError(f"XTTS falló: {result.stderr[-300:]}")

    # Convertir el WAV final a MP3 de calidad
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav),
         "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)],
        check=True, capture_output=True
    )

    import shutil
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


def _postprocess_audio(raw: Path, out: Path, tone: str):
    """
    Post-procesado de audio profesional con FFmpeg (gratis):
    1. Normalización a -16 LUFS (estándar YouTube — sin distorsión, sin silencio)
    2. Compresor dinámico (iguala volumen, elimina picos — voz más uniforme)
    3. Leve reverb de sala (hace la voz más cálida, menos robótica)
    4. Filtro de paso alto 80Hz (elimina rumor de fondo)
    5. Bitrate 192kbps (calidad profesional YouTube)
    """
    import subprocess

    # Parámetros de reverb según el tono
    reverb = {
        "misterio":     "0.3:0.3:50:0.5:0.3:0.3",   # sala oscura, eco medio
        "drama":        "0.4:0.4:60:0.5:0.4:0.3",   # sala grande, dramático
        "motivacional": "0.15:0.15:20:0.4:0.2:0.2", # sala pequeña, íntimo, energético
        "documental":   "0.2:0.2:30:0.4:0.25:0.2",  # sala neutral
        "humor":        "0.1:0.1:15:0.3:0.15:0.15", # muy íntimo, cercano
        "neutro":       "0.2:0.2:25:0.4:0.2:0.2",   # sala estándar
    }.get(tone, "0.2:0.2:25:0.4:0.2:0.2")

    subprocess.run([
        "ffmpeg", "-y", "-i", str(raw),
        "-af", (
            # 1. Filtro de paso alto (elimina bajas frecuencias de fondo)
            "highpass=f=80,"
            # 2. Compresor (voz más uniforme, sin picos)
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=50:makeup=2dB,"
            # 3. Reverb ligero de sala (voz más cálida)
            f"aecho=0.8:0.85:{reverb},"
            # 4. Normalización a -16 LUFS (estándar YouTube)
            "loudnorm=I=-16:TP=-1.5:LRA=11"
        ),
        "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100",
        str(out)
    ], check=True, capture_output=True)


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
