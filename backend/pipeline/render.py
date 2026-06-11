import subprocess
import json
import math
import shutil
import random
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from ..database import add_step

MUSIC_DIR = Path(__file__).parent.parent.parent / "music"

# ── FUENTES PROFESIONALES ─────────────────────────────────────────────────────
# Bebas Neue: documentales Netflix/YouTube (condensado, bold, cinematic)
# Montserrat ExtraBold: subtítulos karaoke modernos
# Oswald Bold: citas y lower thirds secundarios
# DejaVu Mono Bold: efecto máquina de escribir (monoespaciado, siempre disponible)
_F_BEBAS      = "/usr/share/fonts/custom/BebasNeue-Regular.ttf"
_F_MONTSERRAT = "/usr/share/fonts/custom/Montserrat-ExtraBold.ttf"
_F_OSWALD     = "/usr/share/fonts/custom/Oswald-Bold.ttf"
_F_MONO       = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

def _ff(font_path: str) -> str:
    """Devuelve fontfile= para drawtext, con fallback a DejaVu si no existe."""
    import os
    return font_path if os.path.exists(font_path) else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ── CONSTANTES TYPEWRITER ─────────────────────────────────────────────────────
_TW_DT    = 0.065   # segundos entre cada carácter
_TW_SIZE  = 36      # fontsize máquina de escribir
_TW_CW    = 22      # ancho aprox. por carácter en px (monoespaciado a 36px)

# Mapa tono → subcarpeta de música
MUSIC_TONE_MAP = {
    "misterio":     ["misterio", "neutro"],
    "drama":        ["drama", "misterio"],
    "motivacional": ["motivacional", "neutro"],
    "documental":   ["documental", "neutro"],
    "humor":        ["humor", "neutro"],
    "neutro":       ["neutro", "documental"],
}

def _pick_music(tone: str) -> Path | None:
    """Selecciona un MP3 aleatorio de la carpeta del tono. Fallback a carpetas alternativas."""
    folders = MUSIC_TONE_MAP.get(tone, ["neutro"])
    for folder in folders:
        d = MUSIC_DIR / folder
        if d.exists():
            mp3s = list(d.glob("*.mp3"))
            if mp3s:
                return random.choice(mp3s)
    all_mp3s = list(MUSIC_DIR.rglob("*.mp3"))
    return random.choice(all_mp3s) if all_mp3s else None


# ── MÚSICA INTELIGENTE: sincronizada con la emoción del guión ─────────────
# Para cada emoción detectada en el texto, qué keywords de pista y qué volumen usar.
# Volumen: hook más alto (engancha), misterio sutil, tensión audible, clímax fuerte.

_EMOTION_MUSIC = {
    # emoción → (keywords pista preferida, volumen 0-1, fallback keywords)
    "hook":        (["tense-suspense", "tense", "dramatic-intense"],   0.72, ["tense", "dramatic"]),
    "tension":     (["tense-suspense", "tense", "documentary-suspense"], 0.65, ["tense", "suspicious"]),
    "mystery":     (["mysterious", "mystical", "mysterious-cinematic"], 0.55, ["ambient", "dark"]),
    "revelation":  (["documentary-suspense", "tense-suspense"],        0.62, ["tense", "cinematic"]),
    "climax":      (["tense-suspense", "dramatic-intense", "total-war"], 0.70, ["dramatic", "tense"]),
    "calm":        (["dark-hope", "ambient", "documentary-background"], 0.50, ["mysterious", "ambient"]),
    "narrative":   (["documentary-suspense", "documentary-background"], 0.55, ["ambient", "cinematic"]),
    "inspiring":   (["inspiring-cinematic", "epic-uplifting"],          0.60, ["epic", "cinematic"]),
}

# Mapeo de emociones de tts.py → categorías de música
_SSML_TO_MUSIC = {
    "tension":   "tension",
    "mystery":   "mystery",
    "revelation":"revelation",
    "climax":    "climax",
    "short":     "narrative",
    "long":      "narrative",
    "normal":    "narrative",
}

# Duración mínima de una zona musical (evita cambios demasiado frecuentes)
MIN_ZONE_SECS = 45.0
MUSIC_CROSSFADE = 4.0   # segundos de crossfade entre zonas


def _classify_sentence(text: str) -> str:
    """Mismo clasificador que tts.py pero devuelve categoría musical."""
    t = text.lower()
    TENSION = {'murió','muerte','cuerpo','sangre','tragedia','desastre','silencio',
               'oscuridad','jamás','horror','sepult','ceniza','destruid','desapareci',
               'catástrofe','víctimas','muertos','cadáver','peligro','ardió','quemó'}
    MYSTERY = {'misterio','inexplicable','extraño','raro','oculto','prohibido',
               'nadie sabe','nadie lo','se desconoce','jamás','nunca se supo',
               'nunca nadie','secreto','oculto','escondido'}
    REVELATION = {'descubrió','reveló','encontraron','apareció','pero','sin embargo',
                  'increíble','imposible','por primera vez','nunca antes','resulta'}
    CLIMAX = {'millones','miles','siglos','generaciones','toda la historia',
              'cambió todo','lo más importante','lo más impactante','cambia todo',
              'jamás antes','récord','mayor','más grande','nunca visto'}
    INSPIRING = {'esperanza','futuro','posible','logró','superó','victoria',
                 'triunfó','sobrevivió','renació','reconstruyó'}

    if any(w in t for w in CLIMAX):    return "climax"
    if any(w in t for w in TENSION):   return "tension"
    if any(w in t for w in MYSTERY):   return "mystery"
    if any(w in t for w in REVELATION):return "revelation"
    if any(w in t for w in INSPIRING): return "inspiring"
    return "narrative"


def _analyze_script_zones(script: str, audio_dur: float,
                           hook_dur: float = 90.0) -> list[dict]:
    """
    Analiza el guión y devuelve zonas emocionales con timestamps reales.
    Cada zona: {"start": s, "end": s, "emotion": str, "volume": float}

    Zona 0: siempre el hook (0 → hook_dur) con música intensa.
    Resto: agrupadas por emoción dominante, mínimo MIN_ZONE_SECS cada una.
    """
    import re
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', script.strip()) if s.strip()]
    if not sentences:
        return [{"start": 0, "end": audio_dur, "emotion": "narrative", "volume": 0.55}]

    words_total = max(1, len(script.split()))
    secs_per_word = audio_dur / words_total

    # Calcular timestamp de inicio de cada frase
    word_pos = 0
    sent_data = []
    for s in sentences:
        w = len(s.split())
        t_start = word_pos * secs_per_word
        sent_data.append({"text": s, "t": t_start, "emotion": _classify_sentence(s)})
        word_pos += w

    # Zona 0: hook fijo (siempre intensa)
    zones = [{"start": 0.0, "end": hook_dur, "emotion": "hook",
               "volume": _EMOTION_MUSIC["hook"][1]}]

    # Agrupar frases post-hook en zonas por emoción dominante
    current_emotion = None
    zone_start = hook_dur
    emotion_counts: dict = {}

    for sd in sent_data:
        if sd["t"] < hook_dur:
            continue
        em = sd["emotion"]
        emotion_counts[em] = emotion_counts.get(em, 0) + 1

        if current_emotion is None:
            current_emotion = em
            zone_start = max(hook_dur, sd["t"])
            emotion_counts = {em: 1}
        elif em != current_emotion:
            zone_dur = sd["t"] - zone_start
            if zone_dur >= MIN_ZONE_SECS:
                # Cerrar zona actual
                vol = _EMOTION_MUSIC.get(current_emotion, _EMOTION_MUSIC["narrative"])[1]
                zones.append({"start": zone_start, "end": sd["t"],
                               "emotion": current_emotion, "volume": vol})
                current_emotion = em
                zone_start = sd["t"]
                emotion_counts = {em: 1}
            # Si la zona es muy corta, absorber en la actual (no cambiar todavía)

    # Cerrar última zona
    if current_emotion:
        vol = _EMOTION_MUSIC.get(current_emotion, _EMOTION_MUSIC["narrative"])[1]
        zones.append({"start": zone_start, "end": audio_dur + 4,
                       "emotion": current_emotion, "volume": vol})

    return zones


def _build_music_arc(tone: str, duration: float, tmp: Path,
                     script: str = "") -> Path | None:
    """
    Construye la pista de música sincronizada con la emoción real del guión.
    - Zona 0 (hook, 0-90s): música intensa siempre
    - Zonas siguientes: pista seleccionada según emoción detectada en el texto
    - Cada zona tiene su propio volumen óptimo
    - Crossfade suave de 4s entre zonas
    """
    all_mp3s = list(MUSIC_DIR.rglob("*.mp3"))
    if not all_mp3s:
        return None

    # Pistas del tono (búsqueda priorizada en carpetas correctas)
    tone_folders = MUSIC_TONE_MAP.get(tone, ["neutro"])
    tone_mp3s = []
    for folder in tone_folders:
        tone_mp3s.extend(list((MUSIC_DIR / folder).glob("*.mp3")))
    if not tone_mp3s:
        tone_mp3s = all_mp3s

    # Analizar guión para zonas emocionales (o usar arco simple si no hay guión)
    if script:
        zones = _analyze_script_zones(script, duration)
    else:
        zones = [
            {"start": 0,        "end": 90,       "emotion": "hook",     "volume": 0.72},
            {"start": 90,       "end": duration * 0.55, "emotion": "tension", "volume": 0.60},
            {"start": duration * 0.55, "end": duration * 0.82, "emotion": "climax", "volume": 0.65},
            {"start": duration * 0.82, "end": duration + 4,   "emotion": "mystery","volume": 0.52},
        ]

    def _pick_track(emotion: str, exclude: set) -> tuple[Path, float]:
        """Devuelve (track, volume) para la emoción dada."""
        cfg = _EMOTION_MUSIC.get(emotion, _EMOTION_MUSIC["narrative"])
        kws_primary, vol, kws_fallback = cfg
        # Buscar en carpetas del tono primero
        for kws in [kws_primary, kws_fallback]:
            for kw in kws:
                cands = [p for p in tone_mp3s if kw in p.stem and p not in exclude]
                if cands:
                    return random.choice(cands), vol
        # Fallback global
        remaining = [p for p in all_mp3s if p not in exclude]
        return random.choice(remaining or all_mp3s), vol

    # Construir segmentos de audio por zona
    used: set = set()
    segments: list[tuple[Path, float, float]] = []  # (track, duration_secs, volume)

    for z in zones:
        seg_dur = max(8.0, z["end"] - z["start"] + MUSIC_CROSSFADE)
        track, vol = _pick_track(z["emotion"], used)
        used.add(track)
        segments.append((track, seg_dur, z["volume"]))

    if len(segments) == 1:
        track, seg_dur, vol = segments[0]
        out = tmp / "music_arc.mp3"
        _ffmpeg("-stream_loop", "-1", "-t", f"{duration+6:.1f}",
                "-i", str(track),
                "-af", f"loudnorm=I=-20:TP=-1,volume={vol}",
                "-c:a", "libmp3lame", "-b:a", "128k", str(out))
        return out

    # Recortar cada segmento con su volumen óptimo
    trimmed: list[Path] = []
    for i, (track, seg_dur, vol) in enumerate(segments):
        t = tmp / f"arc_zone_{i}.mp3"
        _ffmpeg("-stream_loop", "-1", "-t", f"{seg_dur:.1f}",
                "-i", str(track),
                "-af", f"loudnorm=I=-20:TP=-1,volume={vol}",
                "-c:a", "libmp3lame", "-b:a", "128k", str(t))
        trimmed.append(t)

    # Encadenar zonas con crossfade cinematográfico
    current = trimmed[0]
    for i in range(1, len(trimmed)):
        merged = tmp / f"arc_merge_{i}.mp3"
        _ffmpeg(
            "-i", str(current), "-i", str(trimmed[i]),
            "-filter_complex",
            f"[0][1]acrossfade=d={MUSIC_CROSSFADE}:c1=tri:c2=tri[out]",
            "-map", "[out]", "-b:a", "128k", str(merged)
        )
        current = merged

    # Recortar al largo exacto
    final = tmp / "music_arc_final.mp3"
    _ffmpeg("-i", str(current), "-t", f"{duration+4:.1f}",
            "-af", "afade=t=out:st=" + f"{max(0,duration-5):.1f}:d=5",
            "-c:a", "libmp3lame", "-b:a", "128k", str(final))
    return final

# ── Resolución de salida ────────────────────────────────────────────────────
OUT_W, OUT_H = 1920, 1080   # 1080p Full HD — calidad profesional YouTube
OUT_RES = f"{OUT_W}x{OUT_H}"

MAX_KB_DURATION = 5.5  # Máximo posible (mystery/calm usa 5.5s, siempre n=1)
IMG_SHOT_DUR    = 4.0  # Duración base por imagen (se ajusta por emoción)

def _extract_lower_thirds(script: str, audio_dur: float) -> list[dict]:
    """
    Extrae fechas, lugares y nombres del guión para mostrar como lower thirds.
    Python puro, 0€. Devuelve lista de {"start", "end", "text"}.

    Detecta:
    - Años: "en 1982", "el año 2017", "desde 1910"
    - Lugares: "en Chiapas", "la Ciudad de México", estados mexicanos
    - Nombres propios tras "llamado", "conocido como"
    """
    import re

    ESTADOS_MX = {
        'chiapas','oaxaca','jalisco','veracruz','puebla','guerrero','michoacán',
        'hidalgo','guerrero','tabasco','campeche','yucatán','quintana roo',
        'chihuahua','sonora','sinaloa','durango','zacatecas','san luis potosí',
        'tamaulipas','nuevo león','coahuila','baja california','guanajuato',
        'querétaro','tlaxcala','morelos','colima','nayarit','aguascalientes',
    }
    CIUDADES_MX = {
        'ciudad de méxico','cdmx','guadalajara','monterrey','tijuana','puebla',
        'toluca','mérida','cancún','acapulco','veracruz','oaxaca','tuxtla',
        'tlatelolco','tenochtitlan','teotihuacán','chichén itzá','palenque',
    }

    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    words_total = max(1, len(script.split()))
    secs_per_word = audio_dur / words_total

    results = []
    word_pos = 0
    seen = set()

    for sent in sentences:
        w_count = len(sent.split())
        t_start = word_pos * secs_per_word
        s_low = sent.lower()

        label = None

        # Año con contexto
        year_m = re.search(r'\b(en|el año|desde|hasta|año)\s+(\d{4})\b', s_low)
        if year_m:
            year = year_m.group(2)
            # Buscar lugar en la misma frase
            lugar = None
            for c in CIUDADES_MX:
                if c in s_low:
                    lugar = c.title()
                    break
            if not lugar:
                for e in ESTADOS_MX:
                    if e in s_low:
                        lugar = e.title()
                        break
            if lugar:
                label = f"{lugar}  ·  {year}"
            else:
                label = year

        # Lugar sin año
        elif not label:
            for c in CIUDADES_MX:
                if c in s_low and c not in seen:
                    label = c.title()
                    seen.add(c)
                    break
            if not label:
                for e in ESTADOS_MX:
                    if e in s_low and e not in seen:
                        label = e.title()
                        seen.add(e)
                        break

        if label and t_start > 5:   # No mostrar en los primeros 5s (hook puro)
            t_end = min(audio_dur, t_start + 3.5)
            results.append({"start": t_start, "end": t_end, "text": label})

        word_pos += w_count

    return results

# Duración de imagen por emoción — hook/clímax rápidos, misterio/narrativa lentos
_EMOTION_IMG_DUR = {
    "hook":       2.5,   # Cortes rapidísimos — máximo enganche
    "climax":     3.0,   # Rápido — adrenalina
    "revelation": 3.0,   # Snap cut — impacto
    "tension":    3.5,   # Rápido con peso
    "narrative":  5.0,   # Pausado — el espectador asimila
    "mystery":    5.5,   # Muy lento — deja respirar la imagen
    "inspiring":  4.0,
    "calm":       5.0,
}
KB_WORKERS = 12        # ffmpeg Ken Burns en paralelo
FADE_SEC = 0.15        # fundido ultrarrápido — estilo Shorts
KB_FAST = True


# ── HELPERS DE CALIDAD CINEMATOGRÁFICA ──────────────────────────────────────

def _zone_enable(zones: list, emotions: list) -> str:
    """Genera expresión ffmpeg enable para una lista de emociones."""
    ranges = [(z["start"], z["end"]) for z in zones if z["emotion"] in emotions]
    if not ranges:
        return "0"
    return "+".join(f"between(t\\,{s:.1f}\\,{e:.1f})" for s, e in ranges)


def _build_zone_vf_extras(zones: list, audio_dur: float) -> list[str]:
    """
    Genera filtros ffmpeg adicionales basados en zonas emocionales:
    - Color grade frío en tensión/misterio
    - Color grade cálido en revelaciones
    - Vignette en tensión/misterio (focaliza atención)
    - Film grain sutil siempre (textura cinematográfica)
    """
    extras = []

    # Color frío (azul) en zonas de tensión, misterio y hook
    # Parámetros correctos colorbalance: bs=blue_shadows, bm=blue_midtones, rh=red_highlights
    cold_en = _zone_enable(zones, ["tension", "mystery", "hook"])
    if cold_en != "0":
        extras.append(
            f"colorbalance=bs=0.10:bm=0.05:rh=-0.06:enable='{cold_en}'"
        )
        # Focus pull: desenfoque atmosférico 1.8px en zonas frías (BBC/Nat Geo)
        extras.append(f"gblur=sigma=1.8:enable='{cold_en}'")

    # Color cálido (naranja/dorado) en revelaciones e inspiración
    # rs=red_shadows, rm=red_midtones, rh=red_highlights
    warm_en = _zone_enable(zones, ["revelation", "inspiring"])
    if warm_en != "0":
        extras.append(
            f"colorbalance=rs=0.05:rm=0.02:rh=0.07:enable='{warm_en}'"
        )
        # Hue shift +8° dorado (luz de hora dorada en revelaciones)
        extras.append(f"hue=h=8:s=1.05:enable='{warm_en}'")

    # EQ de alto contraste en clímax (B&W dramático — tensión máxima)
    climax_en = _zone_enable(zones, ["climax"])
    if climax_en != "0":
        extras.append(
            f"eq=contrast=1.22:saturation=0.55:enable='{climax_en}'"
        )

    # Vignette suave en tensión/misterio (bordes oscuros = foco en centro)
    vig_en = _zone_enable(zones, ["tension", "mystery"])
    if vig_en != "0":
        extras.append(f"vignette=angle=PI/5:enable='{vig_en}'")

    # Film grain sutil siempre (textura de documental, -6dB visual)
    extras.append("noise=c0s=5:c0f=t+u")

    return extras


def _build_chapter_cards(script: str, zones: list, audio_dur: float) -> list[dict]:
    """
    Genera tarjetas de capítulo en las transiciones de zona principales.
    Cada tarjeta: texto centrado, 2.5s, en el primer cambio de zona relevante.
    Extrae el título de la primera frase notable del segmento (Python, 0€).
    """
    import re
    if not script or not zones:
        return []

    words = script.split()
    words_total = max(1, len(words))
    secs_per_word = audio_dur / words_total

    # Solo generar tarjeta en transiciones importantes (excluir hook=primera zona)
    cards = []
    prev_em = None
    for z in zones:
        em = z["emotion"]
        if prev_em is None:
            prev_em = em
            continue   # skip hook

        # Solo en cambios de emoción significativos
        if em == prev_em:
            continue
        if z["start"] < 60:
            continue   # muy al inicio

        # Extraer primera frase del segmento de este zona
        w_idx = int(z["start"] / secs_per_word)
        w_idx = min(w_idx, len(words) - 1)
        segment = " ".join(words[w_idx:w_idx + 25])
        sentences = re.split(r'(?<=[.!?])\s+', segment)
        first_sent = sentences[0] if sentences else segment
        # Recortar a máx 6 palabras para el título
        title_words = first_sent.split()[:6]
        title = " ".join(title_words).rstrip(".,!?;:")
        if len(title) < 8:
            continue

        cards.append({
            "start": z["start"],
            "end": z["start"] + 2.5,
            "text": title.upper(),
            "emotion": em,
        })
        prev_em = em

    return cards[:4]   # máximo 4 tarjetas por vídeo


def _detect_historical_sections(script: str, audio_dur: float) -> list[tuple[float, float]]:
    """
    Detecta secciones del guión que narran eventos históricos (pasado).
    Devuelve lista de (start, end) en segundos para aplicar efecto archivo visual.
    Convención documental BBC/Nat Geo: pasado = sepia cálido, presente = frío neutro.
    """
    import re
    if not script:
        return []

    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    words_total = max(1, len(script.split()))
    secs_per_word = audio_dur / words_total

    HIST = re.compile(
        r'\b(en\s+1[0-9]{3}|en\s+el\s+siglo\s+[xviXVI]+|'
        r'hace\s+\d{2,4}\s+(años?|siglos?|décadas?)|'
        r'retrocede(?:mos)?|volvamos\s+al|regresamos\s+al|'
        r'en\s+aquell[ao]|por\s+aquel\s+entonces|en\s+aquellos\s+(tiempos|años)|'
        r'era\s+(colonial|prehispán|prehisp)|'
        r'los?\s+(aztec|maya|olmec|toltec|mexica)\w*|'
        r'nuestros?\s+(ancestros?|antepasados?|abuelos?)|'
        r'los\s+antiguos\s+mexicanos|época\s+prehisp|'
        r'durante\s+la\s+(colonia|conquista|revolución))\b',
        re.IGNORECASE
    )

    sections: list[tuple[float, float]] = []
    word_pos = 0
    for sent in sentences:
        w_count = len(sent.split())
        t_start = word_pos * secs_per_word
        if HIST.search(sent) and t_start > 30:
            w_dur = w_count * secs_per_word
            sections.append((max(30.0, t_start - 1.5), min(audio_dur, t_start + w_dur + 18)))
        word_pos += w_count

    if not sections:
        return []

    # Fusionar secciones próximas (< 10s de diferencia)
    sections.sort()
    merged: list[list[float]] = [list(sections[0])]
    for s, e in sections[1:]:
        if s <= merged[-1][1] + 10:
            merged[-1][1] = max(e, merged[-1][1])
        else:
            merged.append([s, e])

    return [(s, e) for s, e in merged if e - s >= 12]


def _extract_source_citations(script: str, audio_dur: float) -> list[dict]:
    """
    Detecta citas de fuentes institucionales (INAH, UNAM, UNESCO…) en el guión.
    Las muestra como overlay de credibilidad instantánea, igual que los lower thirds.
    """
    import re
    if not script:
        return []

    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    words_total = max(1, len(script.split()))
    secs_per_word = audio_dur / words_total

    INST_RE = re.compile(
        r'\b(INAH|UNAM|UNESCO|NASA|CENAPRED|Conagua|CONANP|SEP|IMSS|'
        r'Instituto\s+Nacional\s+de\s+\w+(?:\s+\w+){0,3}|'
        r'Universidad\s+Nacional\s+\w+(?:\s+\w+){0,2}|'
        r'Secretar[ií]a\s+de\s+\w+(?:\s+\w+){0,2})\b',
        re.IGNORECASE
    )
    CITE_VERB = re.compile(
        r'\b(según|de\s+acuerdo\s+con|inform[oó]|revel[oó]|public[oó]|'
        r'afirm[oó]|se[ñn]al[oó]|declar[oó]|document[oó])\b',
        re.IGNORECASE
    )

    results = []
    seen: set[str] = set()
    word_pos = 0

    for sent in sentences:
        w_count = len(sent.split())
        t_start = word_pos * secs_per_word

        m = INST_RE.search(sent)
        if m and t_start > 20:
            inst = m.group(1).strip()
            key = inst.lower()[:20]
            if key not in seen:
                seen.add(key)
                has_verb = bool(CITE_VERB.search(sent))
                label = (f"Fuente: {inst}" if has_verb else inst)[:42]
                results.append({
                    "start": t_start,
                    "end": min(audio_dur, t_start + 3.5),
                    "text": label,
                })

        word_pos += w_count

    return results[:10]


def _generate_subbass_hits(zones: list, audio_dur: float, tmp: Path) -> Path | None:
    """
    Genera pulsos de sub-bajo (50 Hz) en momentos de clímax/revelación.
    Se siente más que se escucha — el efecto de los trailers de Marvel. Coste 0€.
    """
    targets = [
        z["start"] for z in zones
        if z["emotion"] in ("climax", "revelation") and z["start"] > 10
    ]
    if not targets:
        return None

    # Un pulso: seno 50 Hz con envelope exponencial, 0.9s
    pulse = tmp / "subbass_pulse.wav"
    try:
        # 314.16 = 2*pi*50 Hz. exp(-4*t) = envelope que decae en 0.8s
        _ffmpeg(
            "-f", "lavfi",
            "-i", "aevalsrc=0.45*sin(314.16*t)*exp(-4*t):s=44100:c=stereo:d=0.8",
            "-ar", "44100", "-ac", "2", str(pulse)
        )
    except Exception:
        return None

    # Posicionar cada pulso en su timestamp con adelay
    positioned: list[Path] = []
    for i, t in enumerate(targets[:4]):   # máximo 4 hits
        out = tmp / f"subbass_{i}.wav"
        delay_ms = int(t * 1000)
        try:
            _ffmpeg(
                "-i", str(pulse),
                "-af", f"adelay={delay_ms}|{delay_ms},apad=pad_dur={audio_dur + 2}",
                "-t", f"{audio_dur + 2:.1f}",
                "-ar", "44100", "-ac", "2", str(out)
            )
            positioned.append(out)
        except Exception:
            continue

    if not positioned:
        return None

    result = tmp / "subbass_track.wav"
    try:
        if len(positioned) == 1:
            import shutil as _shutil
            _shutil.copy(str(positioned[0]), str(result))
        else:
            mix_inputs: list[str] = []
            for p in positioned:
                mix_inputs += ["-i", str(p)]
            _ffmpeg(*mix_inputs,
                    "-filter_complex", f"amix=inputs={len(positioned)}:normalize=0",
                    "-t", f"{audio_dur + 1:.1f}",
                    "-ar", "44100", "-ac", "2", str(result))
    except Exception:
        return None

    return result if result.exists() else None


def _lower_third_typewriter(text: str, t0: float, t1: float, y_pos: str = "h-110") -> list[str]:
    """
    Lower third con efecto máquina de escribir profesional.
    - Fondo negro semitransparente centrado
    - Cada carácter aparece en secuencia cada _TW_DT segundos
    - Cursor parpadeante mientras escribe
    - Fuente monoespaciada DejaVu Mono Bold para efecto typewriter auténtico
    """
    text_up = text.upper()
    n = len(text_up)
    total_type = n * _TW_DT
    total_w = n * _TW_CW + 24
    bg_x = f"(w-{total_w})/2"
    bg_y = f"{y_pos}-8"

    layers = []

    # Fondo negro bajo el texto
    layers.append(
        f"drawbox=x='{bg_x}':y='{bg_y}':w={total_w}:h={_TW_SIZE + 20}:"
        f"color=0x000000@0.82:t=fill:"
        f"enable='between(t,{t0:.3f},{t1:.3f})'"
    )

    # Línea decorativa encima del texto (estilo lower-third profesional)
    line_y = f"{y_pos}-10"
    layers.append(
        f"drawbox=x='{bg_x}':y='{line_y}':w={total_w}:h=2:"
        f"color=0xFFD700@0.90:t=fill:"
        f"enable='between(t,{t0:.3f},{t1:.3f})'"
    )

    # Caracteres uno a uno
    for i, char in enumerate(text_up):
        if char == ' ':
            continue
        t_char = t0 + i * _TW_DT
        x_char = f"(w-{total_w})/2 + {i * _TW_CW + 12}"
        char_esc = char.replace("'", "\\'").replace(":", "\\:").replace(",", "\\,").replace("\\", "\\\\")
        layers.append(
            f"drawtext=text='{char_esc}':"
            f"fontfile='{_ff(_F_MONO)}':"
            f"fontsize={_TW_SIZE}:fontcolor=white@0.97:"
            f"x={x_char}:y={y_pos}:"
            f"enable='between(t,{t_char:.3f},{t1:.3f})'"
        )

    # Cursor parpadeante mientras escribe
    cursor_x = f"(w-{total_w})/2 + {n * _TW_CW + 12}"
    cursor_end = t0 + total_type + 0.4
    layers.append(
        f"drawtext=text='_':"
        f"fontfile='{_ff(_F_MONO)}':"
        f"fontsize={_TW_SIZE}:fontcolor=0xFFD700@0.90:"
        f"x={cursor_x}:y={y_pos}:"
        f"enable='between(t,{t0:.3f},{cursor_end:.3f})*eq(mod(floor((t)*8),2),0)'"
    )

    return layers


def _generate_typewriter_audio(lower_thirds: list, audio_dur: float, tmp: Path) -> Path | None:
    """
    Genera pista de audio con ticks de máquina de escribir.
    Crea un loop de ticks y lo activa solo durante los períodos de escritura de cada lower third.
    """
    if not lower_thirds:
        return None

    # Generar tick único (burst de alta frecuencia, 40ms)
    tick = tmp / "tw_tick.wav"
    try:
        _ffmpeg(
            "-f", "lavfi",
            "-i", "aevalsrc=0.20*sin(2*PI*5200*t)*exp(-200*t)+0.08*sin(2*PI*2800*t)*exp(-150*t):s=44100:c=mono:d=0.04",
            "-ar", "44100", "-ac", "2", str(tick)
        )
    except Exception:
        return None

    # Loop del tick a ritmo typewriter (~13 chars/seg)
    loop_dur = audio_dur + 5
    ticks_loop = tmp / "tw_loop.wav"
    try:
        _ffmpeg(
            "-stream_loop", "-1", "-i", str(tick),
            "-t", f"{loop_dur:.1f}",
            "-af", f"aresample=44100",
            "-ar", "44100", "-ac", "2", str(ticks_loop)
        )
    except Exception:
        return None

    # Volumen activo solo cuando hay escritura (entre t0 y t0+n*dt de cada lower third)
    vol_parts = []
    for lt in lower_thirds:
        t0 = lt["start"]
        n_chars = len(lt["text"].replace(' ', ''))
        t_end_typing = min(t0 + n_chars * _TW_DT + 0.1, lt["end"])
        vol_parts.append(f"between(t,{t0:.3f},{t_end_typing:.3f})")

    if not vol_parts:
        return None

    vol_expr = "min(1," + "+".join(vol_parts) + ")"
    result = tmp / "typewriter_track.wav"
    try:
        _ffmpeg(
            "-i", str(ticks_loop),
            "-af", f"volume='{vol_expr}',volume=0.18",  # -0.18 = volumen sutil
            "-t", f"{audio_dur+1:.1f}",
            "-ar", "44100", "-ac", "2", str(result)
        )
        return result if result.exists() and result.stat().st_size > 1000 else None
    except Exception:
        return None


def _generate_heartbeat_track(zones: list, audio_dur: float, tmp: Path) -> Path | None:
    """
    Genera pista de latido cardíaco en zonas de tensión/misterio (numpy vectorizado, 0€).
    BPM sube de 58→74 dentro de cada zona. Volumen a -32 LUFS.
    """
    import wave as _wave
    import numpy as np

    tension_zones = [
        z for z in zones
        if z["emotion"] in ("tension", "mystery", "hook") and z["end"] - z["start"] > 8
    ]
    if not tension_zones:
        return None

    sr = 44100
    n_samples = int(audio_dur * sr) + sr
    pcm = np.zeros(n_samples, dtype=np.float32)

    for z in tension_zones:
        t_start = float(z["start"])
        t_end   = min(float(z["end"]), audio_dur)
        zone_dur = max(1.0, t_end - t_start)
        t = t_start

        while t < t_end:
            progress = (t - t_start) / zone_dur
            bpm = 58 + 16 * progress
            interval = 60.0 / bpm

            # Lub — 75 Hz, decay rápido
            n_lub = min(int(0.09 * sr), n_samples)
            idx0 = int(t * sr)
            if idx0 < n_samples:
                i = np.arange(min(n_lub, n_samples - idx0))
                pcm[idx0:idx0 + len(i)] += 0.38 * np.exp(-11 * i / sr) * np.sin(6.283 * 75 * i / sr)

            # Dub — 90 Hz, más suave
            n_dub = min(int(0.06 * sr), n_samples)
            idx1 = int((t + 0.22) * sr)
            if idx1 < n_samples:
                i = np.arange(min(n_dub, n_samples - idx1))
                pcm[idx1:idx1 + len(i)] += 0.22 * np.exp(-13 * i / sr) * np.sin(6.283 * 90 * i / sr)

            t += interval

    peak = float(np.abs(pcm).max())
    if peak < 0.001:
        return None
    gain = 0.06 / peak

    int16 = np.clip(pcm * gain * 32767, -32767, 32767).astype(np.int16)
    stereo = np.column_stack([int16, int16])

    out = tmp / "heartbeat_track.wav"
    with _wave.open(str(out), 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())

    return out if out.exists() and out.stat().st_size > 1000 else None


def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _ffmpeg(*args):
    result = subprocess.run(["ffmpeg", "-y"] + list(args), capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-10:])
        raise RuntimeError(f"FFmpeg error ({result.returncode}):\n{tail}")
    return result


def _color_grade(tone: str) -> str:
    """Filtro de color grading según el tono narrativo."""
    grades = {
        "misterio":   "eq=contrast=1.12:brightness=-0.06:saturation=0.75,colorchannelmixer=rr=0.9:gg=0.95:bb=1.1",
        "drama":      "eq=contrast=1.15:brightness=-0.08:saturation=0.70,colorchannelmixer=rr=0.85:gg=0.90:bb=1.05",
        "motivacional":"eq=contrast=1.05:brightness=0.02:saturation=1.15",
        "deportivo":  "eq=contrast=1.10:brightness=0.03:saturation=1.25,colorchannelmixer=rr=1.05:gg=0.95:bb=0.85",
        "documental": "eq=contrast=1.08:brightness=-0.02:saturation=0.90",
        "humor":      "eq=contrast=1.0:brightness=0.03:saturation=1.10",
        "neutro":     "eq=contrast=1.05:brightness=-0.01:saturation=0.95",
    }
    return grades.get(tone, grades["neutro"])


def _image_to_clips(img: Path, total_dur: float, tmp_dir: Path,
                    base_idx: int) -> list[Path]:
    """Convierte 1 imagen a N sub-clips con Ken Burns + fade in/out en imagen completa."""
    n = max(1, math.ceil(total_dur / MAX_KB_DURATION))
    clip_dur = total_dur / n
    clips = []

    for j in range(n):
        out = tmp_dir / f"img_{base_idx:03d}_{j}.mp4"
        kb = (_ken_burns_fast if KB_FAST else _ken_burns)(base_idx * 8 + j, clip_dur)

        # Sin fade por clip — los fades por clip crean frames negros en cada corte.
        # El fade global de apertura se aplica en el encode final (fade=t=in en el output).
        vf = kb

        _ffmpeg(
            "-loop", "1", "-i", str(img),
            "-vf", vf,
            "-t", f"{clip_dur:.4f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25",
            "-pix_fmt", "yuv420p",   # formato uniforme → permite concat -c copy (sin re-encode)
            str(out)
        )
        clips.append(out)
    return clips


def _image_to_clips_batch(images: list[Path], img_dur: float,
                           tmp_dir: Path, base_idx: int = 0) -> list[list[Path]]:
    """Procesa todas las imágenes en paralelo con KB_WORKERS workers."""
    results: list[list[Path] | None] = [None] * len(images)

    def work(i: int, img: Path):
        return i, _image_to_clips(img, img_dur, tmp_dir, base_idx + i)

    with ThreadPoolExecutor(max_workers=KB_WORKERS) as ex:
        futs = {ex.submit(work, i, img): i for i, img in enumerate(images)}
        for f in as_completed(futs):
            i, clips = f.result()
            results[i] = clips

    return results  # type: ignore


def _video_to_shot(video: Path, dur: float, out: Path, idx: int = 0,
                   emotion: str = "narrative"):
    """
    Convierte un clip de Pexels en un plano cinematográfico:
    - Slow motion 0.82x en misterio/tensión/narrativa (más cinematográfico)
    - Velocidad normal en hook/clímax (energía y ritmo)
    """
    try:
        vdur = get_duration(video)
    except Exception:
        vdur = 0

    # Slow motion para zonas atmosféricas — más cinematic
    slow = {"mystery": 0.78, "tension": 0.82, "narrative": 0.85, "calm": 0.78}
    slow_factor = slow.get(emotion, 1.0)   # hook y climax = velocidad normal

    if slow_factor < 1.0:
        # setpts aumenta la duración del clip → necesitamos más fuente
        source_needed = dur / slow_factor
        pts = f"setpts={1/slow_factor:.3f}*PTS,"
    else:
        source_needed = dur
        pts = ""

    # Sin fades por clip — evitan frames negros en cada corte al concatenar con -c copy
    vf = (
        f"{pts}scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=25"
    )

    if vdur >= source_needed:
        _ffmpeg("-ss", "0", "-t", f"{source_needed:.2f}", "-i", str(video),
                "-vf", vf, "-t", f"{dur:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25",
                "-pix_fmt", "yuv420p", str(out))
    else:
        _ffmpeg("-stream_loop", "-1", "-t", f"{source_needed:.2f}", "-i", str(video),
                "-vf", vf, "-t", f"{dur:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25",
                "-pix_fmt", "yuv420p", str(out))
    return out


def _img_dur_for_t(t: float, body_offset: float, zones: list) -> float:
    """Devuelve la duración de imagen correcta para el tiempo t del vídeo."""
    abs_t = t + body_offset
    for z in zones:
        if z["start"] <= abs_t < z["end"]:
            return _EMOTION_IMG_DUR.get(z["emotion"], IMG_SHOT_DUR)
    return IMG_SHOT_DUR


def _build_dynamic_body(images: list[Path], videos: list[Path],
                        body_dur: float, tmp: Path,
                        zones: list = None, body_offset: float = 90.0) -> list[Path]:
    """
    Cada imagen aparece EXACTAMENTE UNA VEZ.
    Duración por imagen varía según la emoción de la zona:
    - Hook/clímax: 2.5-3s (rápido, adrenalina)
    - Narrativa: 5s (pausado, asimilar)
    - Misterio: 5.5s (atmosférico)
    """
    _zones = zones or []

    # Calcular duración de cada imagen según su posición en el tiempo
    # Primero calcular cuánto tiempo ocuparán las imágenes en total
    n_img = len(images)
    n_vid = len(videos) if videos else 0
    total_shots_est = n_img + n_vid
    t_per_shot_est = body_dur / max(1, total_shots_est)

    # Asignar duración a cada imagen por zona emocional
    img_durs = []
    for i in range(n_img):
        # Posición estimada en el tiempo para esta imagen
        t_est = (i / max(1, n_img)) * body_dur
        d = _img_dur_for_t(t_est, body_offset, _zones)
        img_durs.append(d)

    img_total = sum(img_durs)
    vid_remaining = max(0, body_dur - img_total)
    per_vid = vid_remaining / max(1, n_vid) if n_vid > 0 else 0

    # Intercalar: 1 clip de vídeo cada N imágenes
    shots: list[tuple[str, Path, float]] = []
    gap = max(1, round(n_img / max(1, n_vid))) if videos else 10**9
    vi = 0
    for i, img in enumerate(images):
        shots.append(("img", img, img_durs[i]))
        if videos and (i + 1) % gap == 0 and vi < len(videos) and per_vid >= 2.0:
            shots.append(("vid", videos[vi], per_vid)); vi += 1
    while videos and vi < len(videos) and per_vid >= 2.0:
        shots.append(("vid", videos[vi], per_vid)); vi += 1

    # Ajuste fino: escalar para que la suma sea exactamente body_dur
    total_planned = sum(d for _, _, d in shots)
    if total_planned > 0 and abs(total_planned - body_dur) > 0.5:
        scale = body_dur / total_planned
        shots = [(k, p, max(1.5, d * scale)) for k, p, d in shots]

    results: list[list[Path] | None] = [None] * len(shots)

    def _get_emotion_for_shot(i: int, total: int) -> str:
        """Estima la emoción de la zona para el shot en posición i."""
        t_est = (i / max(1, total)) * body_dur
        for z in (_zones or []):
            if z["start"] - body_offset <= t_est < z["end"] - body_offset:
                return z["emotion"]
        return "narrative"

    def work(idx: int, kind: str, path: Path, dur: float):
        em = _get_emotion_for_shot(idx, len(shots))
        if kind == "img":
            return idx, _image_to_clips(path, dur, tmp, idx)
        else:
            out = tmp / f"vid_{idx:03d}.mp4"
            try:
                _video_to_shot(path, dur, out, idx, emotion=em)
                return idx, [out]
            except Exception:
                return idx, _image_to_clips(images[idx % len(images)], dur, tmp, 5000 + idx)

    with ThreadPoolExecutor(max_workers=KB_WORKERS) as ex:
        futs = [ex.submit(work, i, k, p, d) for i, (k, p, d) in enumerate(shots)]
        for f in as_completed(futs):
            idx, clips = f.result()
            results[idx] = clips

    ordered = []
    for group in results:
        if group:
            ordered.extend(group)
    return ordered


def render_video(
    job_id: str,
    hook_clips: list[Path],
    body_images: list[Path],
    audio_path: Path,
    output_path: Path,
    title: str,
    hook_images: list[Path] = None,
    tone: str = "neutro",
    body_videos: list[Path] = None,
    progress_cb=None,
    fast_kenburns: bool = True,
    script: str = "",   # guión completo para análisis emocional de música
) -> Path:
    global KB_FAST
    KB_FAST = fast_kenburns
    add_step(job_id, "render", "running",
             f"Iniciando montaje — tono: {tone} · Ken Burns {'rápido' if fast_kenburns else 'calidad máxima'} · {KB_WORKERS} workers…")

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError(f"Audio vacío: {audio_path}")

    valid_body = [i for i in body_images if i.exists() and i.stat().st_size > 5000]
    if len(valid_body) < 3:
        raise RuntimeError(f"Solo {len(valid_body)} imágenes válidas (mínimo 3)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.parent / f"tmp_{job_id}"
    tmp.mkdir(exist_ok=True)

    audio_dur = get_duration(audio_path)
    add_step(job_id, "render", "running",
             f"Audio: {audio_dur/60:.1f} min | {len(valid_body)} imágenes | "
             f"color: {tone}")

    # ── HOOK ──────────────────────────────────────────────────────────────────
    # Los primeros 60-90 segundos son el hook real (no 25s). MrBeast-level retention.
    hook_dur = min(90.0, audio_dur * 0.20)
    hook_concat = tmp / "hook.mp4"

    valid_hook_vids = [v for v in (hook_clips or []) if v.exists() and v.stat().st_size > 0]
    valid_hook_imgs = [i for i in (hook_images or []) if i.exists() and i.stat().st_size > 5000]

    if valid_hook_vids:
        # ── HOOK TRÁILER: cada clip aparece UNA SOLA VEZ ──────────────────────
        # Regla de oro: el cerebro detecta la repetición en 3 segundos.
        # Si no hay suficientes clips únicos, completamos con body clips y luego
        # con imágenes Flux (Ken Burns). NUNCA se recicla un clip.
        clip_len = max(1.5, min(2.5, hook_dur / max(1, len(valid_hook_vids))))
        shots_needed = int(hook_dur / clip_len) + 1  # +1 de margen

        # Pool único: hook clips primero, luego body clips no solapados, luego imágenes
        hook_paths_set = {str(v) for v in valid_hook_vids}
        body_extras = [v for v in (body_videos or []) if v.exists() and v.stat().st_size > 50000 and str(v) not in hook_paths_set]
        img_extras   = valid_body  # imágenes Flux como último recurso

        unique_pool: list = list(valid_hook_vids)
        if len(unique_pool) < shots_needed:
            unique_pool += body_extras[:shots_needed - len(unique_pool)]
        if len(unique_pool) < shots_needed:
            # Marcar imágenes con un tag para distinguirlas de vídeos
            unique_pool += [(img, "img") for img in img_extras[:shots_needed - len(unique_pool)]]

        # Tomar exactamente los shots que necesitamos, sin repetir
        shot_list = unique_pool[:shots_needed]

        trimmed = []
        for k, item in enumerate(shot_list):
            t_out = tmp / f"hvtrim_{k:02d}.mp4"

            # Distinguir imagen de vídeo
            if isinstance(item, tuple):
                img_path, _ = item
                _ffmpeg("-loop", "1", "-i", str(img_path), "-t", f"{clip_len:.2f}",
                        "-vf", (f"{_ken_burns_fast(k, clip_len)},"
                                "scale=1920:1080:force_original_aspect_ratio=increase,"
                                "crop=1920:1080,fps=25,"
                                "eq=contrast=1.22:brightness=-0.04:saturation=1.35"),
                        "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25",
                        "-pix_fmt", "yuv420p", str(t_out))
            else:
                v = item
                # Arrancar desde el mejor tramo del clip (primer tercio = más dinámico)
                try:
                    vdur = get_duration(v)
                    ss = min(1.0, max(0.0, vdur * 0.1))  # empezar al 10%, evitar intro oscura
                except Exception:
                    ss = 0.0

                _ffmpeg("-ss", f"{ss:.2f}", "-i", str(v), "-t", f"{clip_len:.2f}",
                        "-vf", ("scale=1920:1080:force_original_aspect_ratio=increase,"
                                "crop=1920:1080,fps=25,"
                                "eq=contrast=1.22:brightness=-0.04:saturation=1.35"),
                        "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25",
                        "-pix_fmt", "yuv420p", str(t_out))
            trimmed.append(t_out)
        lst = tmp / "hv_list.txt"
        lst.write_text("\n".join(f"file '{v.resolve()}'" for v in trimmed))
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", str(hook_concat))
    elif valid_hook_imgs:
        per_img = hook_dur / len(valid_hook_imgs)
        hook_clip_groups = _image_to_clips_batch(valid_hook_imgs, per_img, tmp, base_idx=900)
        clips_h = [c for group in hook_clip_groups for c in group]
        _write_concat(tmp / "hi_list.txt", clips_h)
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "hi_list.txt"),
                "-c", "copy", str(hook_concat))
    else:
        n_h = min(4, len(valid_body))
        per_h = hook_dur / n_h
        hook_clip_groups = _image_to_clips_batch(valid_body[:n_h], per_h, tmp, base_idx=900)
        clips_h = [c for group in hook_clip_groups for c in group]
        _write_concat(tmp / "hf_list.txt", clips_h)
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "hf_list.txt"),
                "-c", "copy", str(hook_concat))

    # ── CUERPO DINÁMICO: imágenes (Ken Burns) + vídeo real Pexels + Whisper ────
    body_dur = audio_dur - hook_dur
    valid_vids = [v for v in (body_videos or []) if v.exists() and v.stat().st_size > 50000]
    total_shots = len(valid_body) + len(valid_vids)
    add_step(job_id, "render", "running",
             f"Montaje dinámico: {len(valid_body)} imágenes + {len(valid_vids)} clips de vídeo real "
             f"= {total_shots} planos (~{body_dur/max(1,total_shots):.0f}s c/u) · Whisper simultáneo…")

    # ── PROGRESO REAL: cuántos clips Ken Burns se esperan (datos 100% reales) ──
    # Solo img_*.mp4 + vid_*.mp4 = la parte lenta (Ken Burns paralelo)
    # hvtrim_*.mp4 (hook Pexels) son rápidos y no se cuentan
    _per_shot      = body_dur / max(1, total_shots)
    _n_sub_img     = max(1, math.ceil(_per_shot / MAX_KB_DURATION))
    _exp_body      = len(valid_body) * _n_sub_img + len(valid_vids)
    # Hook: solo si usa imágenes (hook con vídeos Pexels no crea img_*.mp4)
    _exp_hook_img  = 0
    if not valid_hook_vids:
        _vh_imgs = valid_hook_imgs if valid_hook_imgs else valid_body[:min(4, len(valid_body))]
        _ph      = hook_dur / max(1, len(_vh_imgs))
        _exp_hook_img = len(_vh_imgs) * max(1, math.ceil(_ph / MAX_KB_DURATION))
    _total_expected = max(1, _exp_body + _exp_hook_img)
    _render_start   = _time.time()
    _stop_monitor   = threading.Event()

    def _count_clips() -> int:
        try:
            return len(list(tmp.glob("img_*.mp4"))) + len(list(tmp.glob("vid_*.mp4")))
        except Exception:
            return 0

    def _monitor_loop():
        _last_done = 0
        while not _stop_monitor.wait(20):
            try:
                done = _count_clips()
                if done <= _last_done:
                    continue
                _last_done = done
                pct     = min(85.0, done / _total_expected * 100.0)
                elapsed = _time.time() - _render_start
                frac    = pct / 100.0
                eta     = (elapsed / frac) * (1.0 - frac) if frac > 0.005 else 0.0
                if progress_cb:
                    progress_cb(pct, eta, done, _total_expected, "clips")
            except Exception:
                pass

    if progress_cb:
        threading.Thread(target=_monitor_loop, daemon=True).start()

    ass_path = tmp / "subs.ass"

    # Lanzar Whisper en background MIENTRAS se procesan los planos
    with ThreadPoolExecutor(max_workers=1) as whisper_ex:
        whisper_fut = whisper_ex.submit(_whisper_subtitles, audio_path, ass_path)

        _script_zones = _analyze_script_zones(script, audio_dur) if script else []
        all_body_clips = _build_dynamic_body(valid_body, valid_vids, body_dur, tmp,
                                              zones=_script_zones, body_offset=hook_dur)

        _write_concat(tmp / "body_list.txt", all_body_clips)
        body_mp4 = tmp / "body.mp4"
        # -c copy: copia directa sin re-encode (todos los clips ya son yuv420p 1080p 25fps)
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "body_list.txt"),
                "-c", "copy", str(body_mp4))

        _write_concat(tmp / "full_list.txt", [hook_concat, body_mp4])
        full_mp4 = tmp / "full.mp4"
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "full_list.txt"),
                "-c", "copy", str(full_mp4))

        subs_ok = whisper_fut.result()

    # ── Fin fase 1 (clip gen) → fase 2 (encode final) ─────────────────────────
    _stop_monitor.set()   # Para el hilo monitor (daemon, muere solo también)
    if progress_cb:
        progress_cb(90.0, 0.0, _total_expected, _total_expected, "encode")

    # Construir arco emocional de música (4 fases con crossfade)
    music_arc = _build_music_arc(tone, audio_dur, tmp, script=script)
    has_music = music_arc is not None and music_arc.exists()

    # ── LOWER THIRDS, COLOR GRADE DINÁMICO, CAPÍTULOS, TÉCNICAS CINEMATOGRÁFICAS ──
    lower_thirds        = _extract_lower_thirds(script, audio_dur) if script else []
    source_citations    = _extract_source_citations(script, audio_dur) if script else []
    historical_sections = _detect_historical_sections(script, audio_dur) if script else []
    _vf_zones           = _analyze_script_zones(script, audio_dur) if script else []
    zone_vf_extras      = _build_zone_vf_extras(_vf_zones, audio_dur)
    chapter_cards       = _build_chapter_cards(script, _vf_zones, audio_dur)

    # Impact overlays — números y palabras clave en pantalla gigante (MrBeast style)
    words_json   = ass_path.parent / "words.json"
    impact_overlays = _extract_impact_overlays(words_json)

    # Sub-bass hits en clímax (se siente más que se escucha — efecto trailer)
    subbass_track = _generate_subbass_hits(_vf_zones, audio_dur, tmp)

    # Heartbeat en zonas de tensión/misterio (Python puro, 0€, -32 LUFS)
    heartbeat_track = _generate_heartbeat_track(_vf_zones, audio_dur, tmp)
    has_hb = heartbeat_track is not None and heartbeat_track.exists()

    # Audio máquina de escribir para lower thirds (ticks sincronizados carácter a carácter)
    typewriter_track = _generate_typewriter_audio(lower_thirds, audio_dur, tmp)
    has_tw = typewriter_track is not None and typewriter_track.exists()

    # Log de estado con todas las variables ya definidas
    n_zones    = len(_vf_zones) if _vf_zones else 4
    n_hist     = len(historical_sections)
    n_bars     = sum(1 for z in _vf_zones if z["emotion"] in ("climax","revelation") and z["end"]-z["start"]>5)
    n_cites    = len(source_citations)
    n_impacts  = len(impact_overlays)
    has_sub_ok = subbass_track is not None and subbass_track.exists()
    music_info = f"🎵 {n_zones} zonas" if has_music else "✗"
    add_step(job_id, "render", "running",
             f"Subtítulos {'✓' if subs_ok else '✗'} · {music_info} · "
             f"histórico={n_hist} · barras={n_bars} · citas={n_cites} · "
             f"impacto={n_impacts} · sub-bass={'✓' if has_sub_ok else '✗'} · "
             f"heartbeat={'✓' if has_hb else '✗'} · encode final…")

    # Re-hook al 32% y al 50% (rescata a los espectadores que están a punto de irse)
    mid_t = audio_dur * 0.32
    mid_hook_filter = (
        f"drawtext=text='Y LO MAS IMPACTANTE VIENE AHORA...':"
        f"fontfile='{_ff(_F_BEBAS)}':"
        f"fontsize=52:fontcolor=white@0.94:"
        f"box=1:boxcolor=black@0.72:boxborderw=18:"
        f"shadowcolor=black@0.40:shadowx=2:shadowy=2:"
        f"x=(w-text_w)/2:y=h*0.87:"
        f"enable='between(t,{mid_t:.0f},{mid_t+4.0:.0f})'"
    )
    mid50_t = audio_dur * 0.50
    mid50_hook_filter = (
        f"drawtext=text='LO QUE VIENE AHORA... NADIE LO SABE.':"
        f"fontfile='{_ff(_F_BEBAS)}':"
        f"fontsize=48:fontcolor=0xFFD700@0.96:"
        f"box=1:boxcolor=black@0.75:boxborderw=16:"
        f"shadowcolor=black@0.40:shadowx=2:shadowy=2:"
        f"x=(w-text_w)/2:y=h*0.87:"
        f"enable='between(t,{mid50_t:.0f},{mid50_t+4.5:.0f})'"
    )

    # Marca de canal — identidad visual (esquina superior derecha)
    watermark_filter = (
        f"drawtext=text='México Oculto':"
        f"fontfile='{_ff(_F_OSWALD)}':"
        f"fontsize=24:fontcolor=white@0.22:"
        f"x=w-text_w-28:y=22"
    )

    # ── MEZCLA FINAL: audio + música + subtítulos + color grade + CTA ─────────
    cta_start = max(0, audio_dur - 12)
    grade = _color_grade(tone)

    def _build_vf(esc_subs: str | None) -> str:
        layers = []
        if esc_subs:
            layers.append(f"subtitles='{esc_subs}'")
        layers.append(grade)

        # Nitidez cinematográfica sutil (luma_amount=0.6): firma visual de documentales
        layers.append("unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.6")

        # ── FADE-IN GLOBAL DE APERTURA (1.5s) ────────────────────────────────
        # Un único fade-in al inicio de todo el vídeo — entrada cinematográfica limpia.
        # Ahora que los clips NO tienen fade propio, este es el único fundido.
        layers.append("fade=t=in:st=0:d=1.5")

        # ── CTA SUSCRIPCIÓN en segundo 15 (pico de retención) ────────────────
        # Momento ideal: el espectador ya enganchó pero aún no se fue.
        # Duración 6s: lo suficiente para leer sin molestar.
        layers.append(
            f"drawtext=text='SUSCRIBETE PARA MAS SECRETOS DE MEXICO':"
            f"fontfile='{_ff(_F_BEBAS)}':"
            f"fontsize=44:fontcolor=white@0.95:"
            f"box=1:boxcolor=0x000000@0.72:boxborderw=14:"
            f"shadowcolor=black@0.50:shadowx=2:shadowy=2:"
            f"x=(w-text_w)/2:y=h*0.05:"
            f"enable='between(t,15,21)'"
        )

        # Color grade dinámico por zona emocional (frío/cálido/clímax)
        layers.extend(zone_vf_extras)

        # ── EFECTO ARCHIVO HISTÓRICO ─────────────────────────────────────────
        # Convención BBC/Nat Geo: pasado = sepia cálido. El cerebro lo lee como
        # "material de archivo real". Se activa solo en secciones históricas detectadas.
        for s, e in historical_sections:
            en = f"between(t,{s:.1f},{e:.1f})"
            layers.append(
                f"colorbalance=rs=0.08:gs=0.03:bs=-0.12:"
                f"rm=0.05:gm=0.02:bm=-0.08:enable='{en}'"
            )
            layers.append(f"eq=saturation=0.70:contrast=1.05:enable='{en}'")

        # ── BARRAS CINEMÁTICAS 2.35:1 ANIMADAS ───────────────────────────────
        # Las barras DESLIZAN desde los bordes en 0.4s → entrada cinéfila elegante.
        # Top bar: crece de h=0 a h=132 desde arriba (y=0 fijo).
        # Bottom bar: aparece desde y=1080 y sube a y=948 (h también crece).
        # Salida: se retraen en 0.3s antes del final de la zona.
        for z in _vf_zones:
            if z["emotion"] in ("climax", "revelation") and z["end"] - z["start"] > 5:
                t0, t1 = z["start"], z["end"]
                slide_in  = 0.40   # duración entrada
                slide_out = 0.30   # duración salida
                # Altura progresiva: 0→132 en slide_in, luego 132 hasta slide_out, 132→0
                h_expr = (
                    f"132*if(lt(t-{t0:.2f},{slide_in}),"
                    f"(t-{t0:.2f})/{slide_in},"
                    f"if(gt(t,{t1:.2f}-{slide_out}),"
                    f"max(0,({t1:.2f}-t)/{slide_out}),"
                    f"1))"
                )
                # Barra superior: x=0, y=0, altura animada
                layers.append(
                    f"drawbox=x=0:y=0:w=iw:h='{h_expr}':color=black:t=fill:"
                    f"enable='between(t,{t0:.2f},{t1:.2f})'"
                )
                # Barra inferior: x=0, y=1080-h_animada, altura animada
                layers.append(
                    f"drawbox=x=0:y='1080-{h_expr}':w=iw:h='{h_expr}':color=black:t=fill:"
                    f"enable='between(t,{t0:.2f},{t1:.2f})'"
                )

        # ── TARJETAS DE CAPÍTULO ANIMADAS: sube desde abajo (Netflix style) ───
        # Entrada: desliza desde h+60 hasta centro en 0.35s
        # Salida: sube y sale por arriba en 0.25s — flujo visual de cine
        for cc in chapter_cards:
            t0, t1 = cc["start"], cc["end"]
            txt = cc["text"].replace("'", "\\'").replace(":", "\\:").replace(",", "\\,")
            si = 0.35   # slide-in
            so = 0.25   # slide-out
            y_expr = (
                f"if(lt(t-{t0:.2f},{si}),"
                f"(h+60)-((h+60)-(h-text_h)/2)*(t-{t0:.2f})/{si},"
                f"if(gt(t,{t1:.2f}-{so}),"
                f"(h-text_h)/2+(-text_h-60-(h-text_h)/2)*(t-{t1:.2f}+{so})/{so},"
                f"(h-text_h)/2))"
            )
            layers.append(
                f"drawtext=text='{txt}':"
                f"fontfile='{_ff(_F_BEBAS)}':"
                f"fontsize=62:fontcolor=white@0.96:"
                f"shadowcolor=black@0.70:shadowx=3:shadowy=3:"
                f"box=1:boxcolor=0x000000@0.78:boxborderw=28:"
                f"x=(w-text_w)/2:y='{y_expr}':"
                f"enable='between(t,{t0:.2f},{t1:.2f})'"
            )

        # ── LOWER THIRDS ANIMADOS: slide desde izquierda, salida por izquierda ─
        # Entrada en 0.28s, salida en 0.18s. Canales de 1M+ los tienen SIEMPRE.
        # ── LOWER THIRDS: efecto máquina de escribir profesional ─────────────
        for lt in lower_thirds:
            t0, t1 = lt["start"], lt["end"]
            layers.extend(_lower_third_typewriter(lt["text"], t0, t1, y_pos="h-108"))

        # ── CITAS DE FUENTES: credibilidad instantánea ─────────────────────
        # "Según el INAH" aparece en pantalla al mencionarlo — da autoridad al instante.
        for sc in source_citations:
            t0, t1 = sc["start"], sc["end"]
            txt = sc["text"].replace("'", "\\'").replace(":", "\\:").replace(",", "\\,")
            layers.append(
                f"drawtext=text='{txt}':"
                f"fontfile='{_ff(_F_OSWALD)}':"
                f"fontsize=28:fontcolor=white@0.90:"
                f"box=1:boxcolor=0x0D2540@0.85:boxborderw=12:"
                f"x=72:y=h-195:"
                f"enable='between(t,{t0:.1f},{t1:.1f})'"
            )

        # ── IMPACT OVERLAYS: números y palabras clave en pantalla gigante ──────
        # Cuando el narrador dice "22 MILLONES" o "1325" → aparece ENORME en pantalla
        # El cerebro registra el dato con doble impacto: lo escucha Y lo ve (MrBeast style)
        for imp in impact_overlays:
            t0  = imp["t"]
            t1  = t0 + imp["dur"]
            txt = imp["text"].replace("'", "\\'").replace(":", "\\:").replace(",", "\\,")
            layers.append(
                f"drawtext=text='{txt}':"
                f"fontfile='{_ff(_F_BEBAS)}':"
                f"fontsize=134:fontcolor=0xFFFFFF@0.97:"
                f"x=(w-text_w)/2:y=h*0.37:"
                f"shadowcolor=0x000000@0.85:shadowx=4:shadowy=4:"
                f"borderw=3:bordercolor=0x00D4FF@0.80:"
                f"enable='between(t,{t0:.2f},{t1:.2f})'"
            )

        # Flash de impacto: destello blanco 80ms justo al aparecer el número
        # El mismo instante que el espectador lee el dato → impacto visual doble
        for imp in impact_overlays[:4]:   # máximo 4 flashes por vídeo
            t0 = imp["t"]
            layers.append(
                f"eq=brightness=0.10:enable='between(t,{t0:.2f},{t0+0.08:.2f})'"
            )

        # ── ZOOM PUNCH en impactos (4% zoom instantáneo, 0.28s) ─────────────
        # MrBeast usa esto en cada dato grande: el frame SALTA hacia el espectador.
        # scale con eval=frame permite cambiar tamaño por fotograma.
        if impact_overlays:
            pulses = [
                f"between(t,{imp['t']:.2f},{imp['t']+0.28:.2f})"
                for imp in impact_overlays[:5]
            ]
            pulse_expr = f"min(1,{'+'.join(pulses)})"
            layers.append(
                f"scale=w='1920+76*{pulse_expr}':h='1080+43*{pulse_expr}':eval=frame,"
                f"crop=1920:1080:x='(iw-1920)/2':y='(ih-1080)/2'"
            )

        # ── SCREEN SHAKE en momentos más intensos (0.35s) ────────────────────
        # El frame tiembla ±6px — el cerebro lo lee como "impacto físico".
        # Usamos scale(1932x1092)+crop para tener margen de pixeles donde agitar.
        if impact_overlays and len(impact_overlays) >= 1:
            shakes = [
                f"between(t,{imp['t']:.2f},{imp['t']+0.35:.2f})"
                for imp in impact_overlays[:3]
            ]
            shake_expr = f"min(1,{'+'.join(shakes)})"
            layers.append(
                f"scale=1932:1092,"
                f"crop=1920:1080:"
                f"x='6+6*{shake_expr}*sin(2*3.14159*t*17)':"
                f"y='6+4*{shake_expr}*sin(2*3.14159*t*13+1.57)'"
            )

        # ── RACK FOCUS en transiciones DRAMÁTICAS de zona ────────────────────
        # Solo en cambios emocionales SIGNIFICATIVOS (narrative→climax, mystery→revelation…)
        # Máximo 4 transiciones para no saturar la cadena de filtros.
        # Dos pasos: blur fuerte (0.12s) → blur suave (0.18s) → nítido.
        # Convención Nat Geo: el re-enfoque señala que "algo importante está pasando".
        RACK_FOCUS_MAX = 4
        DRAMATIC_CHANGES = {
            ("narrative", "climax"), ("tension", "revelation"), ("mystery", "climax"),
            ("narrative", "revelation"), ("calm", "climax"), ("hook", "revelation"),
            ("tension", "climax"), ("mystery", "revelation"),
        }
        rack_count = 0
        last_rack_t = -120.0  # mínimo 2 min entre rack focus (no saturar)
        for i in range(1, len(_vf_zones)):
            if rack_count >= RACK_FOCUS_MAX:
                break
            prev_em = _vf_zones[i-1]["emotion"]
            curr_em = _vf_zones[i]["emotion"]
            t_trans = _vf_zones[i]["start"]
            is_dramatic = (prev_em, curr_em) in DRAMATIC_CHANGES
            if is_dramatic and 30 < t_trans < audio_dur - 30 and t_trans - last_rack_t > 120:
                layers.append(
                    f"gblur=sigma=5:enable='between(t,{t_trans-0.05:.2f},{t_trans+0.12:.2f})'"
                )
                layers.append(
                    f"gblur=sigma=2:enable='between(t,{t_trans+0.12:.2f},{t_trans+0.30:.2f})'"
                )
                rack_count += 1
                last_rack_t = t_trans

        # ── REVELATION ZOOM: zoom lento durante revelaciones ─────────────────
        # Máximo 3 zonas — las más largas (más larga = más impacto del zoom).
        # El zoom es lineal 0%→2.5% en la duración. Técnica BBC Documentary.
        _rev_zones = sorted(
            [z for z in _vf_zones
             if z["emotion"] in ("revelation", "climax") and z["end"] - z["start"] > 10],
            key=lambda z: z["end"] - z["start"], reverse=True
        )[:3]   # solo las 3 zonas más largas
        for z in _rev_zones:
            t0, t1 = z["start"], z["end"]
            dur = max(1.0, t1 - t0)
            zoom_val = (
                f"max(0,min(1,(t-{t0:.1f})/{dur:.1f}))"
                f"*between(t,{t0:.1f},{t1:.1f})"
            )
            layers.append(
                f"scale=w='1920+48*{zoom_val}':h='1080+27*{zoom_val}':eval=frame,"
                f"crop=1920:1080:x='(iw-1920)/2':y='(ih-1080)/2'"
            )

        # ── CHROMATIC ABERRATION en impactos (rgbashift) ─────────────────────
        # En el momento de mayor impacto, los canales RGB se desplazan ligeramente.
        # El mismo efecto de los trailers de ciencia ficción y terror. 0.25s.
        if impact_overlays:
            for imp in impact_overlays[:3]:
                t0 = imp["t"]
                layers.append(
                    f"rgbashift=rh=4:bh=-4:"
                    f"enable='between(t,{t0:.2f},{t0+0.22:.2f})'"
                )

        # ── SATURATION DRAIN en tensión (hue filter progresivo) ──────────────
        # La saturación baja gradualmente dentro de zonas de tensión/misterio.
        # Más desaturado = más amenaza visual. El espectador lo siente sin verlo.
        # Se recupera solo al salir de la zona (el clip siguiente tiene saturación normal).
        for z in _vf_zones:
            if z["emotion"] in ("tension", "mystery") and z["end"] - z["start"] > 12:
                t0, t1 = z["start"], z["end"]
                dur = max(1.0, t1 - t0)
                # Saturación baja de 1.0 a 0.75 progresivamente
                sat_drain = (
                    f"1.0-0.25*max(0,min(1,(t-{t0:.1f})/{dur:.1f}))"
                    f"*between(t,{t0:.1f},{t1:.1f})"
                )
                layers.append(f"hue=s='{sat_drain}'")

        # ── HOOKS DE RETENCIÓN: 32% y 50% ───────────────────────────────────
        layers.append(mid_hook_filter)
        layers.append(mid50_hook_filter)

        # ── MARCA DE CANAL (watermark 15% opacidad) ─────────────────────────
        layers.append(watermark_filter)

        # ── CTA AL FINAL ──────────────────────────────────────────────────────
        layers.append(
            f"drawtext=text='SUSCRIBETE Y ACTIVA LA CAMPANITA':"
            f"fontfile='{_ff(_F_BEBAS)}':"
            f"fontsize=58:fontcolor=white@0.97:"
            f"box=1:boxcolor=0xCC0000@0.90:boxborderw=22:"
            f"shadowcolor=black@0.50:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=h*0.05:"
            f"enable='between(t,{cta_start:.1f},{audio_dur:.1f})'"
        )

        # ── FADE TO BLACK FINAL (2.5s) ────────────────────────────────────────
        # Cierre cinematográfico limpio. Cada gran película termina con negro total.
        # Lo aplica DESPUÉS del CTA → el rojo se apaga también con el fade.
        layers.append(f"fade=t=out:st={max(0, audio_dur - 2.5):.1f}:d=2.5")

        return ",".join(layers)

    if subs_ok:
        esc = str(ass_path.resolve()).replace("'", "\\'").replace(":", "\\:")
        vf = _build_vf(esc)
    else:
        vf = _build_vf(None)

    # ── SFX BOOM + SONIDO AMBIENTE ────────────────────────────────────────────
    sfx_path = tmp / "sfx_boom.wav"
    amb_path = tmp / "ambient.wav"

    # 1. Boom de impacto (0.4s) — igual que antes
    try:
        _ffmpeg(
            "-f", "lavfi",
            "-i", "anoisesrc=color=brown:amplitude=0.9:duration=0.5",
            "-af", "lowpass=f=90,aeval=val(0)*pow(max(0,1-t/0.4),1.5):c=same,"
                   "loudnorm=I=-6:TP=-1",
            "-ar", "44100", "-ac", "2", str(sfx_path)
        )
    except Exception:
        sfx_path = None

    # 2. Sonido ambiente generado con ffmpeg según el tema del guión (coste 0€)
    # Detectar tipo de ambiente por palabras clave en el guión
    amb_type = "neutral"
    if script:
        s_low = script.lower()
        if any(w in s_low for w in ["ciudad","metro","calle","tráfico","urbano","edificio"]):
            amb_type = "urban"
        elif any(w in s_low for w in ["cueva","túnel","cenote","subterráneo","bajo tierra","oscuridad"]):
            amb_type = "underground"
        elif any(w in s_low for w in ["volcán","erupción","lava","sismo","terremoto","temblor"]):
            amb_type = "volcanic"
        elif any(w in s_low for w in ["selva","río","bosque","naturaleza","agua","lluvia","viento"]):
            amb_type = "nature"
        elif any(w in s_low for w in ["mar","océano","playa","costa","barco"]):
            amb_type = "ocean"

    # Generar ambiente sintético con ffmpeg (ruidos filtrados = 0€)
    AMB_RECIPES = {
        "urban":       ("anoisesrc=color=white:amplitude=0.15", "bandpass=f=1200:w=800,volume=0.3"),
        "underground": ("anoisesrc=color=brown:amplitude=0.2",  "lowpass=f=300,aecho=0.8:0.9:500:0.3,volume=0.25"),
        "volcanic":    ("anoisesrc=color=brown:amplitude=0.3",  "lowpass=f=120,volume=0.2"),
        "nature":      ("anoisesrc=color=pink:amplitude=0.2",   "bandpass=f=800:w=600,volume=0.3"),
        "ocean":       ("anoisesrc=color=pink:amplitude=0.25",  "lowpass=f=400,aecho=0.5:0.7:200:0.2,volume=0.28"),
        "neutral":     ("anoisesrc=color=brown:amplitude=0.08", "lowpass=f=200,volume=0.15"),
    }
    src, af = AMB_RECIPES.get(amb_type, AMB_RECIPES["neutral"])
    try:
        _ffmpeg(
            "-f", "lavfi", "-i", f"{src}:duration={audio_dur+4:.1f}",
            "-af", f"{af},afade=t=in:st=0:d=3,afade=t=out:st={max(0,audio_dur-4):.1f}:d=4,"
                   "loudnorm=I=-42:TP=-1",  # muy sutil: -42 LUFS, solo se "siente"
            "-ar", "44100", "-ac", "2", str(amb_path)
        )
    except Exception:
        amb_path = None

    if has_music:
        # Mezcla: voz + música + boom + ambiente + sub-bass hits
        has_sfx    = sfx_path and sfx_path.exists()
        has_amb    = amb_path and amb_path.exists()
        has_sub    = subbass_track and subbass_track.exists()

        inputs = ["-i", str(full_mp4), "-i", str(audio_path), "-i", str(music_arc)]
        extra_idx = 3

        if has_sfx:
            inputs += ["-i", str(sfx_path)]
            sfx_label = f"[{extra_idx}:a]"; extra_idx += 1
        if has_amb:
            inputs += ["-i", str(amb_path)]
            amb_label = f"[{extra_idx}:a]"; extra_idx += 1
        if has_sub:
            inputs += ["-i", str(subbass_track)]
            sub_label = f"[{extra_idx}:a]"; extra_idx += 1
        if has_hb:
            inputs += ["-i", str(heartbeat_track)]
            hb_label = f"[{extra_idx}:a]"; extra_idx += 1
        if has_tw:
            inputs += ["-i", str(typewriter_track)]
            tw_label = f"[{extra_idx}:a]"; extra_idx += 1

        # ── MÚSICA FIX: convertir voz mono→stereo antes del amix ─────────────
        # La voz XTTS es MONO. Si hacemos amix(mono_voz + stereo_música),
        # ffmpeg colapsa todo a mono y la música casi desaparece.
        # Solución: upconvert la voz a stereo → amix opera en stereo → output stereo.
        fc_parts = []
        fc_parts.append("[1:a]aformat=channel_layouts=stereo[voice]")
        mix_labels = ["[voice]"]   # voz ahora stereo

        # Música: subir volumen para que se escuche claramente bajo la narración
        fc_parts.append("[2:a]highpass=f=80,volume=1.4,afade=t=in:st=0:d=4[mus]")
        mix_labels.append("[mus]")

        # SFX boom (impacto al inicio)
        if has_sfx:
            fc_parts.append(f"{sfx_label}atrim=0:0.4,apad=pad_len=1,adelay=0|0[sfx]")
            mix_labels.append("[sfx]")

        # Ambiente
        if has_amb:
            fc_parts.append(f"{amb_label}highpass=f=60[amb]")
            mix_labels.append("[amb]")

        # Sub-bass hits en clímax (40-60 Hz, se siente más que se escucha)
        if has_sub:
            fc_parts.append(f"{sub_label}highpass=f=30,lowpass=f=80,volume=0.58[sub]")
            mix_labels.append("[sub]")

        # Heartbeat en tensión (-32 LUFS, subconsciente, latido cardíaco)
        if has_hb:
            fc_parts.append(f"{hb_label}lowpass=f=200,volume=0.9[hb]")
            mix_labels.append("[hb]")

        # Ticks de máquina de escribir sincronizados con lower thirds
        if has_tw:
            fc_parts.append(f"{tw_label}highpass=f=1000,volume=1.0[tw]")
            mix_labels.append("[tw]")

        n_mix = len(mix_labels)
        fc_parts.append(f"{''.join(mix_labels)}amix=inputs={n_mix}:normalize=0[mx]")
        fc_parts.append("[mx]alimiter=limit=0.97[aout]")
        fc = ";".join(p for p in fc_parts if p)

        _ffmpeg(
            *inputs,
            "-filter_complex", fc,
            "-map", "0:v", "-map", "[aout]",
            "-vf", vf,
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
            "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path)
        )
    else:
        # Sin música: mezcla voz + sub-bass si hay clímax (siempre stereo)
        has_sub = subbass_track and subbass_track.exists()
        if has_sub:
            _ffmpeg(
                "-i", str(full_mp4),
                "-i", str(audio_path),
                "-i", str(subbass_track),
                "-filter_complex",
                "[1:a]aformat=channel_layouts=stereo[v];[v][2:a]amix=inputs=2:normalize=0[mx];[mx]alimiter=limit=0.97[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-vf", vf,
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
                "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest",
                str(output_path)
            )
        else:
            _ffmpeg(
                "-i", str(full_mp4),
                "-i", str(audio_path),
                "-vf", vf,
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
                "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-shortest",
                str(output_path)
            )

    try:
        shutil.rmtree(tmp)
    except Exception:
        pass

    size_mb = output_path.stat().st_size / 1024 / 1024

    # ── AUTO-GENERAR SHORT VERTICAL (gratis, segunda fuente de ingresos) ──────
    short_path = output_path.parent / "short.mp4"
    try:
        add_step(job_id, "render", "running", "Generando Short vertical 9:16 automático…")
        _generate_short(output_path, short_path, audio_dur)
        short_mb = short_path.stat().st_size / 1024 / 1024
        add_step(job_id, "render", "done",
                 f"✅ Vídeo {size_mb:.0f}MB · Short {short_mb:.0f}MB · "
                 f"{audio_dur/60:.0f} min · subtítulos {'✓' if subs_ok else '✗'}", 0)
    except Exception as e:
        add_step(job_id, "render", "done",
                 f"Vídeo listo: {size_mb:.0f} MB · {audio_dur/60:.0f} min "
                 f"(Short falló: {str(e)[:40]})", 0)

    return output_path


def _generate_short(video_path: Path, output_path: Path, total_dur: float):
    """
    Genera el Short vertical 9:16 exactamente con el hook del vídeo (primeros 55s).
    El hook es el mejor contenido para TikTok/Shorts — ya está diseñado para enganchar.
    Añade CTA al final y subtítulos grandes automáticos.
    """
    # Short = exactamente el hook (primeros 55s máximo)
    hook_end = min(55.0, total_dur)
    actual_dur = hook_end

    # Short vertical 9:16 — exactamente el hook
    # Subtítulos más grandes para móvil + CTA al final
    cta_t = max(0, actual_dur - 7)
    vf_short = (
        "scale=-1:1920,crop=1080:1920,"
        f"drawtext=text='VER VÍDEO COMPLETO ▶':"
        f"fontcolor=white:fontsize=48:box=1:boxcolor=black@0.75:boxborderw=12:"
        f"x=(w-text_w)/2:y=h*0.86:enable='between(t,{cta_t:.0f},{actual_dur:.0f})',"
        f"drawtext=text='¡SUSCRÍBETE\\!':"
        f"fontcolor=0xFFE000:fontsize=52:box=1:boxcolor=black@0.75:boxborderw=12:"
        f"x=(w-text_w)/2:y=h*0.92:enable='between(t,{cta_t:.0f},{actual_dur:.0f})'"
    )

    _ffmpeg(
        "-ss", "0", "-t", f"{actual_dur:.2f}",
        "-i", str(video_path),
        "-vf", vf_short,
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
        "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    )


# ── Limpieza de disco ────────────────────────────────────────────────────────

def cleanup_intermediates(job_dir: Path) -> float:
    """
    Tras renderizar: borra archivos de trabajo que ya no se necesitan
    (el vídeo final ya los contiene). Devuelve MB liberados.
    Conserva: final.mp4, short.mp4, thumbnail*.jpg, metadata.json
    """
    freed = 0.0
    targets = [
        job_dir / "narration.mp3",
        job_dir / "images",
        job_dir / "hook_images",
        job_dir / "hook_videos",
        job_dir / "body_videos",
        job_dir / "thumbnail_base.jpg",
    ]
    # Carpetas temporales que pudieran haber quedado de un fallo
    targets += list(job_dir.glob("tmp_*"))
    targets += list(job_dir.glob("_xtts_tmp"))
    targets += list(job_dir.glob("_short_tmp"))

    for t in targets:
        try:
            if not t.exists():
                continue
            if t.is_dir():
                freed += sum(f.stat().st_size for f in t.rglob("*") if f.is_file())
                shutil.rmtree(t)
            else:
                freed += t.stat().st_size
                t.unlink()
        except Exception:
            pass
    return freed / 1024 / 1024


def cleanup_after_upload(job_dir: Path) -> float:
    """
    Tras subir a YouTube: borra TODO lo pesado — el contenido ya está en YouTube.
    Conserva solo: metadata.json, thumbnail.jpg/b/c (registro ligero, ~500KB total).
    Devuelve MB liberados.
    """
    freed = 0.0

    # ── Archivos individuales a borrar ────────────────────────────────────────
    big_files = [
        "final.mp4",       # ~150-250MB — ya está en YouTube
        "short.mp4",       # ~100MB — ya está (o se puede regenerar)
        "narration.mp3",   # ~48MB — se puede regenerar si hace falta
        "narration.raw.mp3",
    ]
    for name in big_files:
        p = job_dir / name
        try:
            if p.exists():
                freed += p.stat().st_size
                p.unlink()
        except Exception:
            pass

    # ── Directorios pesados (imágenes, clips Pexels, temporales) ─────────────
    big_dirs = [
        "images",          # ~8MB — 40 imágenes Flux
        "hook_images",     # ~2MB
        "hook_videos",     # ~150MB — clips Pexels
        "body_videos",     # ~150MB — clips Pexels cuerpo
        "voices",          # temp si se generó algo
    ]
    for name in big_dirs:
        d = job_dir / name
        try:
            if d.exists() and d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file():
                        freed += f.stat().st_size
                shutil.rmtree(d)
        except Exception:
            pass

    # ── Directorios temporales de render ──────────────────────────────────────
    for tmp in job_dir.glob("tmp_*"):
        try:
            if tmp.is_dir():
                for f in tmp.rglob("*"):
                    if f.is_file():
                        freed += f.stat().st_size
                shutil.rmtree(tmp)
        except Exception:
            pass

    freed_mb = freed / 1024 / 1024
    # Log en un archivo pequeño de resumen
    try:
        summary = job_dir / "upload_summary.txt"
        summary.write_text(
            f"Subido a YouTube. {freed_mb:.0f} MB liberados del servidor.\n"
            f"Conservado: metadata.json + thumbnails (~500KB)\n"
        )
    except Exception:
        pass

    return freed_mb


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_concat(path: Path, clips: list[Path]):
    path.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))


def _whisper_subtitles(audio_path: Path, ass_path: Path) -> bool:
    try:
        from faster_whisper import WhisperModel
        import json
        model = WhisperModel("base", device="cpu", compute_type="int8")
        # vad_filter=False — el VAD suprime audio mezclado (voz+música) y devuelve 0 palabras
        segments, _ = model.transcribe(str(audio_path), language="es",
                                       word_timestamps=True, vad_filter=False,
                                       condition_on_previous_text=True)
        # Extraer todas las palabras en orden cronológico
        all_words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    if w.word.strip():
                        all_words.append((w.start, w.end, w.word.strip()))

        ass_path.write_text(_build_ass(all_words), encoding="utf-8")

        # Guardar timestamps de palabras para impact overlays
        words_json = ass_path.parent / "words.json"
        words_json.write_text(
            json.dumps([[ws, we, w] for ws, we, w in all_words]),
            encoding="utf-8"
        )

        return len(all_words) > 0
    except Exception:
        return False


def _extract_impact_overlays(words_json: Path) -> list[dict]:
    """
    Lee los timestamps de Whisper y detecta números y palabras de impacto.
    Devuelve lista de {"t": float, "dur": float, "text": str} para mostrar
    en pantalla gigante cuando el narrador los dice.

    Técnica de MrBeast y canales de 1M+: el dato más impactante aparece
    grande en pantalla exactamente cuando se dice. El cerebro lo registra
    con doble impacto: lo escucha Y lo ve.
    """
    import re, json
    if not words_json.exists():
        return []

    try:
        all_words = json.loads(words_json.read_text(encoding="utf-8"))
    except Exception:
        return []

    NUMBER_RE  = re.compile(r'^\d[\d.,]*$')
    # Palabras que van ANTES de un número (contexto)
    BEFORE_NUM = {'en', 'de', 'durante', 'hace', 'año', 'hasta', 'desde',
                  'unos', 'unas', 'más', 'menos', 'casi', 'sobre', 'bajo',
                  'a', 'el', 'la', 'los', 'las', 'un', 'una'}
    # Power words que aparecen solas
    POWER_ALONE = {
        'millones', 'miles', 'siglos', 'toneladas', 'metros', 'kilómetros',
        'kilómetros', 'hectáreas', 'años', 'días', 'horas', 'minutos',
        'enterrada', 'enterrado', 'prohibido', 'prohibida', 'oculto', 'oculta',
        'destruida', 'destruido', 'olvidada', 'olvidado', 'perdida', 'perdido',
        'jamás', 'nunca', 'único', 'única', 'primera', 'primero',
        'mayor', 'más grande', 'más profundo', 'más antigua',
    }

    overlays = []
    words = [(float(ws), float(we), str(w)) for ws, we, w in all_words]
    seen_times: set[float] = set()

    for i, (ws, we, w) in enumerate(words):
        wl = w.lower().strip('.,!?;:«»"\'')

        # Detectar número → mostrar con contexto anterior si hay
        if NUMBER_RE.match(wl) and ws > 30:
            # Contexto: si la palabra anterior es una unidad, añadirla
            display = wl
            if i + 1 < len(words):
                next_w = words[i+1][2].lower().strip('.,!?;:')
                if next_w in {'millones', 'mil', 'miles', 'metros', 'kilómetros',
                               'años', 'toneladas', 'siglos', 'personas',
                               'habitant', 'hectáreas'}:
                    display = wl + " " + next_w.upper()
                    we = words[i+1][1]  # extender duración
            display = display.upper()

            # Evitar overlays demasiado cercanos
            too_close = any(abs(ws - t) < 8.0 for t in seen_times)
            if not too_close:
                overlays.append({"t": ws, "dur": max(1.2, we - ws + 0.4), "text": display})
                seen_times.add(ws)

        # Detectar power words solas
        elif wl in POWER_ALONE and ws > 30:
            too_close = any(abs(ws - t) < 10.0 for t in seen_times)
            if not too_close:
                overlays.append({"t": ws, "dur": 1.0, "text": wl.upper()})
                seen_times.add(ws)

    # Máximo 12 overlays por vídeo — no saturar
    overlays.sort(key=lambda x: x["t"])
    return overlays[:12]


def _clean_word(w: str) -> str:
    """Limpia una palabra para mostrar: elimina puntuación, llaves, barras. Solo texto."""
    w = w.replace("{","").replace("}","").replace("\\","").strip()
    w = w.rstrip('.,!?;:«»"\'-–—').lstrip('«»"\'-–—').strip()
    return w.upper()


def _build_ass(words: list) -> str:
    """
    Subtítulos KARAOKE TikTok/YouTube — palabra activa AMARILLA, resto BLANCO.
    Exactamente igual que los canales de 1M+ en español.

    Sistema:
    - 3 palabras por slide — el espectador ve el contexto completo
    - La palabra que SE ESTÁ DICIENDO ahora → amarilla (#FFE000 / &H0000F0FF)
    - Las otras 2 palabras del slide → blancas
    - Cada palabra tiene su propio Dialogue con timing exacto de Whisper
    - Hook (0-15s): 96px, mayor presencia visual
    - Resto: 80px, legible y limpio

    Técnica usada por MrBeast, Dross, todos los canales virales en español.
    """
    WORDS_PER_SLIDE = 3
    HOOK_END = 15.0
    YELLOW = "&H0000F0FF"   # Amarillo cálido — igual al primary color anterior
    WHITE  = "&H00FFFFFF"   # Blanco puro

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Montserrat ExtraBold — subtítulos estilo Netflix/YouTube premium
        # Bold=-1 (bold inline), outline grueso + sombra suave
        "Style: Big,Montserrat,96,&H00FFFFFF,&H0000F0FF,&H00000000,&HAA000000,"
        "-1,0,0,0,100,100,0.5,0,1,7,4,2,80,80,200,1\n"
        "Style: Main,Montserrat,80,&H00FFFFFF,&H0000F0FF,&H00000000,&H88000000,"
        "-1,0,0,0,100,100,0.3,0,1,5,3,2,60,60,180,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )

    clean = [(ws, we, _clean_word(w)) for ws, we, w in words if _clean_word(w)]
    lines = []

    for i in range(0, len(clean), WORDS_PER_SLIDE):
        chunk = clean[i:i + WORDS_PER_SLIDE]
        if not chunk:
            continue

        style    = "Big" if chunk[0][0] < HOOK_END else "Main"
        group_gs = chunk[0][0]

        # Calcular el final del slide completo (sin solapar el siguiente)
        raw_ge = chunk[-1][1] + 0.04
        next_i  = i + WORDS_PER_SLIDE
        if next_i < len(clean):
            group_ge = min(raw_ge, clean[next_i][0] - 0.02)
        else:
            group_ge = raw_ge
        group_ge = max(group_ge, group_gs + 0.08)

        # Una línea Dialogue por cada palabra del grupo
        # → sólo la palabra "activa" aparece en amarillo
        for j, (ws, we_j, _) in enumerate(chunk):
            # Ventana temporal de esta palabra
            w_start = max(group_gs, ws)
            if j + 1 < len(chunk):
                # La siguiente palabra empieza en chunk[j+1][0]
                w_end = min(we_j + 0.03, chunk[j + 1][0] - 0.01)
            else:
                w_end = group_ge   # última palabra: hasta el final del slide
            w_end = max(w_end, w_start + 0.04)

            # Construir texto: palabra j en AMARILLO, resto en BLANCO
            parts = []
            for k, (_, _, wk) in enumerate(chunk):
                if k == j:
                    parts.append(f"{{\\c{YELLOW}}}{wk}{{\\c{WHITE}}}")
                else:
                    parts.append(wk)
            text = " ".join(parts)

            # Fade in sólo en la primera palabra del slide (0→30ms)
            # Fade out sólo en la última (30ms→0)
            if j == 0:
                fade = "{\\fad(30,0)}"
            elif j == len(chunk) - 1:
                fade = "{\\fad(0,30)}"
            else:
                fade = ""

            lines.append(
                f"Dialogue: 0,{_t(w_start)},{_t(w_end)},{style},,0,0,0,,"
                f"{{\\an2}}{fade}{text}"
            )

    return header + "\n".join(lines) + "\n"


def _t(seconds: float) -> str:
    s = max(0.0, seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{int(sec):02d}.{int((sec%1)*100):02d}"


def _ken_burns_fast(idx: int, dur: float) -> str:
    """
    Ken Burns DRAMÁTICO — movimiento claramente visible en 4 segundos.
    Lienzo 3200×1800 (zoom óptico + panning rápido sobre canvas grande).
    Cada uno de los 12 efectos es notablemente diferente al anterior.
    """
    D = max(0.1, dur)
    fx = [
        # Panning horizontales rápidos (640px = 33% del canvas)
        f"scale=3200:1800,crop=1920:1080:x='40+700*t/{D:.3f}':y=360",                  # → rápido
        f"scale=3200:1800,crop=1920:1080:x='740-700*t/{D:.3f}':y=360",                  # ← rápido
        # Panning verticales pronunciados
        f"scale=3200:1800,crop=1920:1080:x=640:y='580-540*t/{D:.3f}'",                  # ↑ fuerte
        f"scale=3200:1800,crop=1920:1080:x=640:y='40+540*t/{D:.3f}'",                   # ↓ fuerte
        # Diagonales dramáticas
        f"scale=3200:1800,crop=1920:1080:x='40+700*t/{D:.3f}':y='40+540*t/{D:.3f}'",   # ↘ drama
        f"scale=3200:1800,crop=1920:1080:x='40+700*t/{D:.3f}':y='580-540*t/{D:.3f}'",  # ↗ drama
        f"scale=3200:1800,crop=1920:1080:x='740-700*t/{D:.3f}':y='40+540*t/{D:.3f}'",  # ↙ drama
        f"scale=3200:1800,crop=1920:1080:x='740-700*t/{D:.3f}':y='580-540*t/{D:.3f}'", # ↖ drama
        # Zoom in (de esquina a centro)
        f"scale=3200:1800,crop=1920:1080:x='600-550*t/{D:.3f}':y='480-420*t/{D:.3f}'", # zoom centro
        # Revelación (arranca en detalle, abre al panorama)
        f"scale=3200:1800,crop=1920:1080:x='100+550*t/{D:.3f}':y='80+400*t/{D:.3f}'",  # abre
        # Barrido lento cinematográfico (inicio lento, acelera)
        f"scale=3200:1800,crop=1920:1080:x='40+700*(t/{D:.3f})*(t/{D:.3f})':y=200",    # easing
        # Contrapicado-paneo (parte alta del canvas)
        f"scale=3200:1800,crop=1920:1080:x='380+360*t/{D:.3f}':y='40+300*t/{D:.3f}'",  # contrapicado
    ]
    return fx[idx % len(fx)]


def _ken_burns(idx: int, dur: float) -> str:
    """12 efectos distintos (zoompan — CALIDAD MÁXIMA, lento). dur limitado por _image_to_clips."""
    d = max(1, int(dur * 25))
    fx = [
        f"scale=2880:1620,zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)+((iw/zoom/4)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='max(1.3-0.001*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)-((iw/zoom/4)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='min(zoom+0.0008,1.25)':x='iw/2-(iw/zoom/2)':y='ih-(ih/zoom)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)':y='ih/zoom*(0.9-0.4*on/{d})':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='min(zoom+0.001,1.3)':x='iw*0.1':y='ih*0.1':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='(iw/zoom/2)*(0.6+0.8*on/{d})':y='(ih/zoom/2)*(0.6+0.8*on/{d})':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='1.1+0.1*sin(3.14*on/{d})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)':y='ih/zoom*(0.5+0.4*on/{d})':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='min(zoom+0.001,1.3)':x='iw*0.7':y='ih*0.7':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='max(1.2-0.0005*on,1.05)':x='iw/2-(iw/zoom/2)+((iw/zoom/6)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
    ]
    return fx[idx % len(fx)]
