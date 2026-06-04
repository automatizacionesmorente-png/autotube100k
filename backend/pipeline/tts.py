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

# ── 18 perfiles de voz disponibles ──────────────────────────────────────────
# Arquetipos (por contenido — auto-detectados por título+nicho):
#   locutor_deportivo      → Manolo Lama style: fútbol, deportes, hazañas
#   narrador_misterio_iker → Iker Jiménez style: misterios, OVNIS, fenómenos
#   cronista_truecrime     → True crime: crímenes, casos, investigaciones
#   locutor_historico      → History Channel: historia, guerras, imperios
#   coach_motivacional     → Tony Robbins ES: éxito, dinero, superación
#   divulgador_ciencia     → Carl Sagan ES: ciencia, tecnología, IA
#   narrador_conspiracion  → Conspiraciones, secretos, poder oculto
#   narrador_drama_humano  → Dramas, tragedias, historias personales
#
# Perfiles originales (por tono seleccionado en UI):
#   misterio_masculino, drama_masculino, motivacional_masculino,
#   documental_femenino, humor_femenino, neutro_profesional,
#   truecrime_femenino, historia_masculino, conspiracion_masculino, ciencia_femenino

# VOZ UNIVERSAL: voz HUMANA REAL (LibriVox, dominio público) elegida por el usuario.
# Se usa en TODOS los vídeos (las voces de arquetipo eran sintéticas Edge TTS = robóticas).
# Prioridad: voz subida por el usuario > esta voz universal > arquetipo > tono.
# Para volver a voces por arquetipo: poner UNIVERSAL_VOICE = None.
UNIVERSAL_VOICE = "voz_gaspar"

# Mapa tono UI → perfil base (fallback si la auto-detección falla)
XTTS_TONE_MAP = {
    "misterio":     "narrador_misterio_iker",
    "drama":        "narrador_drama_humano",
    "motivacional": "coach_motivacional",
    "documental":   "locutor_historico",
    "humor":        "humor_femenino",
    "neutro":       "neutro_profesional",
}

# Keywords para auto-detección del mejor arquetipo narrador
ARCHETYPE_KEYWORDS = {
    "locutor_deportivo": [
        "fútbol", "mundial", "champions", "liga", "deportes", "gol", "selección",
        "balón", "entrenador", "partido", "copa", "euro", "atleta", "campeón",
        "la roja", "real madrid", "barça", "barcelona", "equipo"
    ],
    "narrador_misterio_iker": [
        "misterio", "inexplicable", "fenómeno", "extraño", "ovni", "ufo",
        "sobrenatural", "paranormal", "enigma", "secreto", "oculto", "desaparecido",
        "fantasma", "maldición", "profecía", "cuarto milenio"
    ],
    "cronista_truecrime": [
        "crimen", "asesinato", "caso", "víctima", "asesino", "detective",
        "investigación", "desaparición", "secuestro", "serial", "criminal",
        "alcàsser", "matar", "homicidio", "forense", "sospechoso", "juicio"
    ],
    "locutor_historico": [
        "historia", "guerra", "imperio", "civilización", "antiguo", "medieval",
        "siglo", "batalla", "rey", "conquistador", "revolución", "roma", "egipto",
        "segunda guerra", "nazi", "soviético", "urss", "chernóbil", "hitler"
    ],
    "coach_motivacional": [
        "éxito", "millonario", "rico", "dinero", "motivación", "superar",
        "lograr", "emprender", "empresario", "hábito", "mentalidad", "cambia",
        "transformar", "sueño", "meta", "objetivo", "productividad"
    ],
    "divulgador_ciencia": [
        "ciencia", "universo", "física", "tecnología", "ia", "inteligencia artificial",
        "robot", "descubrimiento", "investigación científica", "nasa", "espacio",
        "quantum", "cerebro", "evolución", "biología", "química", "algoritmo"
    ],
    "narrador_conspiracion": [
        "conspiración", "illuminati", "gobierno", "ocultan", "verdad prohibida",
        "censurado", "silenciado", "deep state", "control", "manipulación",
        "élite", "nuevos orden", "vigilancia", "resetear", "agenda oculta"
    ],
    "narrador_drama_humano": [
        "drama", "tragedia", "vida", "familia", "amor", "pérdida", "superación",
        "enfermedad", "muerte", "suicidio", "divorci", "abandono", "soledad",
        "historia real", "testimonio", "sobreviviente"
    ],
}


def detect_best_archetype(title: str, niche: str, tone: str) -> str:
    """
    Detecta automáticamente el mejor arquetipo narrador para el contenido.
    Analiza título + nicho con keywords. Fallback al mapa de tono.
    """
    text = (title + " " + niche).lower()
    scores = {}
    for archetype, keywords in ARCHETYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[archetype] = score

    if scores:
        best = max(scores, key=scores.get)
        # Solo usar si hay match claro (score >= 1)
        wav = Path(VOICES_DIR) / f"{best}.wav"
        if wav.exists():
            return best

    # Fallback al mapa de tono
    return XTTS_TONE_MAP.get(tone, "neutro_profesional")


def generate_audio(job_id: str, script: str, tone: str, output_path: Path,
                   custom_ref: str = None, title: str = "", niche: str = "") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output_path.with_suffix(".raw.mp3")

    # ── Reutilizar voz ya generada (evita repetir ~50 min si algo falló después) ──
    if output_path.exists() and output_path.stat().st_size > 100_000:
        add_step(job_id, "tts", "done",
                 f"Voz ya generada · reutilizada · 0.00€", 0)
        return output_path
    # Si existe el audio crudo pero falló el post-proceso, solo re-procesar
    if raw_path.exists() and raw_path.stat().st_size > 100_000:
        try:
            add_step(job_id, "tts", "running", "Re-procesando voz ya generada…")
            _postprocess_audio(raw_path, output_path, tone)
            raw_path.unlink(missing_ok=True)
            add_step(job_id, "tts", "done", "Voz recuperada y procesada · 0.00€", 0)
            return output_path
        except Exception:
            pass  # si falla, regenerar normalmente abajo

    # ── Intentar XTTS v2 primero (calidad narrador profesional, gratis) ───────
    if _xtts_available():
        try:
            # 1. Voz personalizada del usuario (máxima prioridad)
            # 2. Auto-detección por título+nicho (narrador perfecto para el contenido)
            # 3. Fallback al mapa de tono UI
            if custom_ref and Path(custom_ref).exists():
                voice_profile = "voz personalizada (clonada)"
                ref_wav = custom_ref
            elif UNIVERSAL_VOICE and Path(f"{VOICES_DIR}/{UNIVERSAL_VOICE}.wav").exists():
                voice_profile = UNIVERSAL_VOICE + " (humana real)"
                ref_wav = f"{VOICES_DIR}/{UNIVERSAL_VOICE}.wav"
            elif title or niche:
                voice_profile = detect_best_archetype(title, niche, tone)
                ref_wav = f"{VOICES_DIR}/{voice_profile}.wav"
            else:
                voice_profile = XTTS_TONE_MAP.get(tone, "neutro_profesional")
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
    Post-procesado de audio LIMPIO Y CLARO con FFmpeg (gratis).
    SIN reverb — el reverb (aecho) era lo que hacía que la voz sonara "borrosa".
    Cadena de locución profesional (broadcast):
    1. Paso alto 85Hz — quita rumble/graves de fondo
    2. EQ corta el "barro" (~300Hz) que emborrona la voz
    3. EQ realza la PRESENCIA (~3.2kHz) → cada palabra se entiende nítida
    4. EQ leve de "aire" (~9kHz) → claridad y brillo
    5. De-esser suave (controla sibilancias swithout harshness)
    6. Compresor suave (voz uniforme, sin picos)
    7. Normalización -16 LUFS (estándar YouTube)
    8. Bitrate 192kbps
    """
    import subprocess

    subprocess.run([
        "ffmpeg", "-y", "-i", str(raw),
        "-af", (
            "highpass=f=90,"
            # quitar barro/cajón (~280 Hz) que enturbia la voz
            "equalizer=f=280:t=q:w=1.2:g=-2,"
            # realce de presencia/inteligibilidad (~3.5 kHz) — consonantes nítidas
            "equalizer=f=3500:t=q:w=1.3:g=3,"
            # EXCITADOR ARMÓNICO — genera agudos que XTTS no produce (24kHz apagado).
            # Es lo que convierte la voz "borrosa" en NÍTIDA y cristalina.
            "aexciter=amount=2.5:blend=2:freq=7000,"
            # realce de agudos/aire (brillo y claridad)
            "treble=g=3:f=8000,"
            # compresor (voz uniforme, sin picos)
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=80:makeup=2dB,"
            # normalización estándar YouTube
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
