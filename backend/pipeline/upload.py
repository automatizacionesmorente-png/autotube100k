import os
import anthropic
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from ..database import add_step, get_conn


def generate_metadata(job_id: str, script: str, title: str, niche: str,
                      audio_duration: float = None) -> dict:
    """
    Genera descripción + capítulos SINCRONIZADOS con el audio real + hashtags + tags.
    Si se pasa audio_duration, los timestamps de los capítulos se calculan
    proporcionalmente a la duración real del vídeo (no inventados).
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Extraer palabras clave del nicho para fallback
    niche_keywords = [w.strip() for w in niche.replace(',', ' ').split() if len(w.strip()) > 3]

    msg = client.messages.create(
        model="claude-sonnet-4-5-20251001",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""Título: {title}
Nicho: {niche}
Canal: México Oculto — geografía, historia y misterios de México

GUIÓN (primeros 5000 caracteres para contexto):
{script[:5000]}

Genera metadatos YouTube SEO. Responde EXACTAMENTE con este formato — sin markdown, sin explicaciones:

===DESCRIPCION===
[4 párrafos en español mexicano, mínimo 200 palabras en total.
Párrafo 1: Gancho impactante que invite a ver el vídeo (2-3 frases).
Párrafo 2: Los 3 descubrimientos más impactantes del vídeo (sin spoilers).
Párrafo 3: Contexto histórico/geográfico breve.
Párrafo 4: Por qué importa hoy + CTA: "📌 SUSCRÍBETE a México Oculto 🇲🇽 Nuevo vídeo cada día."]

===CAPITULOS===
[10 capítulos en formato EXACTO: número_de_porcentaje|Nombre del capítulo
El primero SIEMPRE es 0|Introducción. Ejemplo real:
0|Introducción
8|El secreto enterrado
18|1325: El nacimiento de Tenochtitlán
...]

===HASHTAGS===
[Exactamente en UNA línea separados por espacio: #MéxicoOculto #México #Historia #Geografía #Documental #CDMX más 8 hashtags específicos del tema]

===TAGS===
[20 tags separados por coma, sin #, máximo 30 chars cada uno. Mezcla términos amplios y específicos del tema en español]"""
        }]
    )
    text = msg.content[0].text

    # Parser robusto para formato ===SECCION===
    import re
    def _extract(section: str) -> str:
        m = re.search(rf"===\s*{section}\s*===\s*(.*?)(?====|\Z)", text, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    desc        = _extract("DESCRIPCION")
    chapters_raw= _extract("CAPITULOS")
    hashtags    = _extract("HASHTAGS")
    tags_raw    = _extract("TAGS")

    chapters = _build_chapters(chapters_raw, audio_duration)

    # ── Fallbacks robustos ───────────────────────────────────────────────────
    if not desc or len(desc) < 80:
        desc = (
            f"¿Qué esconde {title}?\n\n"
            f"En este documental exploramos los secretos mejor guardados de México: {niche}. "
            f"Una historia fascinante que te hará ver México con otros ojos.\n\n"
            f"📌 SUSCRÍBETE a México Oculto para descubrir los secretos mejor guardados de México. "
            f"🇲🇽 Nuevo vídeo cada día."
        )

    if not hashtags or "#" not in hashtags:
        kw_tags = " ".join(f"#{k.capitalize()}" for k in niche_keywords[:5])
        hashtags = f"#MéxicoOculto #México #Historia #Geografía #Documental {kw_tags}"

    # Asegurar que los hashtags están correctamente formateados (uno por línea o en línea)
    # YouTube acepta ambos formatos; usamos una línea para compactar
    hashtags_clean = " ".join(
        f"#{h.lstrip('#')}" for h in re.split(r'[\s,]+', hashtags) if h.strip().lstrip('#')
    )

    if not tags_raw:
        # Fallback: generar tags desde el título y nicho
        tags_raw = f"{title}, {niche}, México, historia de México, documental México, geografía México, México oculto, secretos México, cultura mexicana, historia azteca"

    # Construir descripción final con capítulos
    parts = [desc]
    if chapters:
        parts.append(f"📖 CONTENIDO DEL VÍDEO:\n{chapters}")
    parts.append(hashtags_clean)
    full_description = "\n\n".join(parts)

    tags_list = _sanitize_tags(tags_raw)

    # Garantía mínima de tags — si _sanitize_tags filtró todo, usar fallback directo
    if not tags_list:
        tags_list = _sanitize_tags(
            f"México, historia México, documental México, geografía México, {title}, {niche}, "
            "secretos México, cultura mexicana, México oculto, misterios México"
        )

    return {
        "description": full_description,
        "hashtags": hashtags_clean,
        "tags": tags_list,
    }


def _build_chapters(chapters_raw: str, audio_duration: float = None) -> str:
    """
    Convierte 'PORCENTAJE|Nombre' a timestamps MM:SS reales usando la duración
    del audio. Si no hay duración o el formato falla, usa el texto tal cual.
    YouTube exige: primer capítulo en 00:00 y mínimo 3 capítulos crecientes.
    """
    lines = [l.strip() for l in chapters_raw.splitlines() if l.strip()]
    parsed = []
    for l in lines:
        if "|" in l:
            pct_str, name = l.split("|", 1)
            try:
                pct = max(0.0, min(100.0, float(pct_str.strip().replace("%", ""))))
                parsed.append((pct, name.strip()))
            except ValueError:
                continue

    # Sin duración o sin datos parseables → devolver tal cual (limpiando el "|")
    if not parsed or not audio_duration or audio_duration <= 0:
        return "\n".join(l.replace("|", " ", 1).strip() for l in lines) or chapters_raw

    # Asegurar que empieza en 0 y es creciente
    parsed.sort(key=lambda x: x[0])
    if parsed[0][0] != 0:
        parsed.insert(0, (0.0, "Introducción"))

    out = []
    last_sec = -1
    for pct, name in parsed:
        sec = int(pct / 100.0 * audio_duration)
        if sec <= last_sec:           # garantizar timestamps estrictamente crecientes
            sec = last_sec + 1
        last_sec = sec
        m, s = divmod(sec, 60)
        out.append(f"{m:02d}:{s:02d} {name}")
    return "\n".join(out)


def upload_to_youtube(
    job_id: str,
    video_path: Path,
    thumbnail_path: Path,
    title: str,
    metadata: dict,
    channel_id: str,
) -> str:
    add_step(job_id, "upload", "running", "Subiendo vídeo a YouTube...")

    creds = _get_channel_credentials(channel_id)
    if not creds:
        add_step(job_id, "upload", "skipped",
                 "Canal sin credenciales — configura YouTube OAuth en Canales")
        return None

    youtube = build("youtube", "v3", credentials=creds)

    description = metadata.get("description", "") + "\n\n" + metadata.get("hashtags", "")
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": metadata.get("tags", []),
            "categoryId": "27",   # 27 = Education (mejor para monetización que 22=People)
            "defaultLanguage": "es",
            "defaultAudioLanguage": "es",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # Declaración obligatoria de contenido AI
        }
    }

    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4",
        resumable=True, chunksize=10 * 1024 * 1024
    )

    def _do_insert(b):
        req = youtube.videos().insert(part="snippet,status", body=b, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return resp

    try:
        response = _do_insert(body)
    except Exception as e:
        if "invalidTags" in str(e) or "keyword" in str(e).lower():
            # Reintentar sin tags
            add_step(job_id, "upload", "running", "Tags inválidos — reintentando sin tags…")
            body["snippet"]["tags"] = []
            response = _do_insert(body)
        else:
            raise

    video_id = response["id"]

    if thumbnail_path and thumbnail_path.exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")
            ).execute()
        except Exception:
            pass  # la miniatura es opcional, no fallar si no funciona

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    studio_url  = f"https://studio.youtube.com/video/{video_id}/edit"
    add_step(job_id, "upload", "done", f"✅ Subido — revisa en YouTube Studio: {studio_url}", 0)
    return youtube_url, studio_url


def _get_channel_credentials(channel_id: str) -> Credentials | None:
    if not channel_id:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT access_token, refresh_token FROM channels WHERE id = ? AND connected = 1",
        (channel_id,)
    ).fetchone()
    conn.close()
    if not row or not row["access_token"]:
        return None
    return Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("YOUTUBE_CLIENT_ID"),
        client_secret=os.environ.get("YOUTUBE_CLIENT_SECRET"),
    )


def _sanitize_tags(tags_raw: str) -> list[str]:
    """
    Limpia los tags para que YouTube los acepte.
    YouTube solo acepta: letras, números, espacios. Sin comas, #, símbolos.
    Max 30 chars/tag, max 500 chars total.
    """
    import re
    # Limpiar todo lo que no sea letra/número/espacio
    raw = tags_raw.replace("#", " ").replace('"', " ").replace("'", " ")
    raw = raw.replace(";", ",")  # normalizar separadores alternativos
    candidates = [t.strip() for t in raw.split(",") if t.strip()]
    clean = []
    total_chars = 0
    for tag in candidates:
        # Solo ASCII letras/números + espacios (lo más seguro para YouTube API)
        tag = re.sub(r"[^a-zA-Z0-9áéíóúüñÁÉÍÓÚÜÑ\s]", " ", tag)
        tag = " ".join(tag.split())  # normalizar espacios múltiples
        tag = tag[:30].strip()
        if len(tag) < 2:
            continue
        if total_chars + len(tag) + 1 > 490:
            break
        clean.append(tag)
        total_chars += len(tag) + 1
        if len(clean) >= 15:
            break
    return clean


def _parse_sections(text: str) -> dict:
    """
    Extrae las 4 secciones de forma ROBUSTA: tolera acentos (DESCRIPCIÓN),
    markdown (**DESCRIPCION:**, ## TAGS), mayúsculas/minúsculas y espacios.
    Devuelve {DESCRIPCION, CAPITULOS, HASHTAGS, TAGS}.
    """
    import re
    # Normalizar saltos de línea
    headers = {
        "DESCRIPCION": r"DESCRIPCI[OÓ]N",
        "CAPITULOS":   r"CAP[IÍ]TULOS",
        "HASHTAGS":    r"HASHTAGS",
        "TAGS":        r"TAGS",
    }
    # Encontrar la posición de inicio de cada cabecera
    positions = []
    for key, pat in headers.items():
        # cabecera al inicio de línea, con posible markdown/##/** y dos puntos
        m = re.search(rf"(?im)^\s*[*#>\-\s]*{pat}\s*[*#]*\s*:", text)
        if m:
            positions.append((m.start(), m.end(), key))
    positions.sort()
    result = {k: "" for k in headers}
    for i, (start, end, key) in enumerate(positions):
        # El contenido va desde el final de esta cabecera hasta la siguiente
        next_start = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[end:next_start]
        # Limpiar restos de markdown al principio (**, ##, :) y espacios
        content = re.sub(r"^[\s*#:>\-]+", "", content)
        result[key] = content.strip()
    return result
