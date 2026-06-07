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
UNIVERSAL_VOICE = "voz_alex_mx"   # voz mexicana real (35s referencia) — existe en /voices/

# Mapa tono UI → perfil base (fallback si la auto-detección falla)
XTTS_TONE_MAP = {
    "misterio":     "narrador_misterio_iker",
    "drama":        "narrador_drama_humano",
    "motivacional": "coach_motivacional",
    "documental":   "locutor_historico",
    "humor":        "humor_femenino",
    "neutro":       "neutro_profesional",
    "deportivo":    "locutor_deportivo",   # Manolo Lama style: fútbol, hazañas, épica
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
    # Si existe audio crudo de XTTS (>50MB = calidad real) re-procesar sin regenerar
    # IMPORTANTE: descartar archivos pequeños (<50MB) que son fallbacks de Edge TTS
    if raw_path.exists():
        raw_mb = raw_path.stat().st_size / 1024 / 1024
        if raw_mb > 50:   # XTTS produce >50MB para 30+ min; Edge TTS <15MB
            try:
                add_step(job_id, "tts", "running",
                         f"Re-procesando voz XTTS ya generada ({raw_mb:.0f}MB)…")
                _postprocess_audio(raw_path, output_path, tone)
                raw_path.unlink(missing_ok=True)
                add_step(job_id, "tts", "done", "Voz XTTS recuperada · 0.00€", 0)
                return output_path
            except Exception:
                pass
        else:
            # Audio crudo pequeño = era Edge TTS (voz mala) → borrarlo y regenerar con XTTS
            add_step(job_id, "tts", "running",
                     f"Audio previo era Edge TTS ({raw_mb:.0f}MB) → descartado, regenerando con XTTS…")
            raw_path.unlink(missing_ok=True)

    # ── XTTS v2: hasta 3 intentos antes de caer en Edge TTS ──────────────────
    if _xtts_available():
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

        last_err = None
        for attempt in range(1, 4):   # 3 intentos
            try:
                add_step(job_id, "tts", "running",
                         f"Generando voz XTTS v2 · '{voice_profile}' · intento {attempt}/3…")
                # Limpiar raw parcial de intento anterior
                if raw_path.exists():
                    raw_path.unlink(missing_ok=True)
                _xtts_generate(script, raw_path, ref_wav)
                add_cost_event(job_id, "xtts_v2", len(script), 0, 0)
                _postprocess_audio(raw_path, output_path, tone)
                raw_path.unlink(missing_ok=True)
                size_mb = output_path.stat().st_size / 1024 / 1024
                add_step(job_id, "tts", "done",
                         f"✅ Audio XTTS v2: {size_mb:.1f} MB · {voice_profile} · 0.00€", 0)
                return output_path
            except Exception as e:
                last_err = e
                add_step(job_id, "tts", "running",
                         f"XTTS intento {attempt} falló: {str(e)[:80]}")

        add_step(job_id, "tts", "running",
                 f"⚠ XTTS falló 3 veces ({str(last_err)[:60]}). Usando Edge TTS como último recurso…")

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
    Cadena de audio broadcast profesional para narrador mexicano de YouTube.

    0. silenceremove — elimina el silencio inicial que XTTS v2 genera antes de hablar
       (sin esto: voz empieza en s=2 pero subtítulos en s=0 → desync)
    1. Highpass 80Hz — elimina rumble sin quitar calidez
    2. EQ +3dB @ 150Hz — CUERPO y gravedad de narrador masculino mexicano
    3. EQ -3dB @ 300Hz — reduce nasalidad/cajón de XTTS
    4. EQ +4dB @ 2500Hz — PRESENCIA (consonantes nítidas)
    5. EQ +1dB @ 5000Hz — CLARIDAD suave (fricativas sin harshness)
    6. EQ +1dB @ 9000Hz — AIRE (brillo de micrófono de estudio)
    7. aexciter — humaniza la voz, añade armónicos que XTTS suprime
    8. acompressor broadcast — voz uniforme, punchy, sin aplastamiento
    9. alimiter — seguridad digital
    10. loudnorm -14 LUFS — presencia fuerte en YouTube
    """
    import subprocess

    subprocess.run([
        "ffmpeg", "-y", "-i", str(raw),
        "-af", (
            # 0. CRÍTICO: elimina silencio inicial de XTTS (sincroniza voz + subtítulos)
            "silenceremove=1:0:-40dB,"
            # 1. Eliminar rumble de fondo
            "highpass=f=80,"
            # 2. CALIDEZ — cuerpo y gravedad de voz masculina mexicana
            "equalizer=f=150:t=q:w=0.8:g=3,"
            # 3. Reducir nasalidad/cajón de XTTS
            "equalizer=f=300:t=q:w=1.0:g=-3,"
            # 4. PRESENCIA — claridad de consonantes
            "equalizer=f=2500:t=q:w=1.2:g=4,"
            # 5. CLARIDAD suave — fricativas sin harshness
            "equalizer=f=5000:t=q:w=1.1:g=1,"
            # 6. AIRE natural — brillo de estudio
            "equalizer=f=9000:t=q:w=0.8:g=1,"
            # 7. Excitador armónico — humaniza, añade textura
            "aexciter=amount=2:blend=2:freq=5000,"
            # 8. Compresor broadcast — voz uniforme, no aplastada
            "acompressor=threshold=-18dB:ratio=3:attack=8:release=80:makeup=7dB,"
            # 9. Limitador de seguridad
            "alimiter=limit=0.97,"
            # 10. Loudnorm (-14 LUFS = presencia fuerte en YouTube)
            "loudnorm=I=-14:TP=-1.0:LRA=7"
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
