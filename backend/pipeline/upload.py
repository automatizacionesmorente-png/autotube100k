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
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1100,
        messages=[{
            "role": "user",
            "content": f"""Título del vídeo: {title}
Nicho: {niche}

GUIÓN COMPLETO (para ubicar los capítulos):
{script[:6000]}

Genera metadatos optimizados para YouTube SEO:

DESCRIPCION:
Escribe 3-4 párrafos atractivos con palabras clave naturales sobre el tema.
Incluye al final: "📌 SUSCRÍBETE para más contenido sobre {niche}"

CAPITULOS:
Escribe 8 capítulos. Para cada uno indica el PORCENTAJE del guión donde empieza
(0 a 100) y el nombre, en formato "PORCENTAJE|Nombre del capítulo".
El primero SIEMPRE es "0|Introducción". Ejemplo:
0|Introducción
12|El origen del misterio
...
Basa el porcentaje en dónde aparece ese contenido dentro del guión de arriba.

HASHTAGS:
Exactamente 15 hashtags relevantes separados por espacio. Empieza cada uno con #.
Mezcla hashtags amplios (#YouTube, #{niche.replace(' ','')}) con específicos del tema.

TAGS:
15 tags SEO separados por comas, sin # (para el campo tags de YouTube).

Usa exactamente esas cuatro etiquetas en mayúsculas."""
        }]
    )
    text = msg.content[0].text
    sec = _parse_sections(text)
    desc = sec.get("DESCRIPCION", "").strip()
    chapters_raw = sec.get("CAPITULOS", "").strip()
    hashtags = sec.get("HASHTAGS", "").strip()
    tags_raw = sec.get("TAGS", "").strip()

    chapters = _build_chapters(chapters_raw, audio_duration)

    # Fallback: si la descripción salió vacía, no subir sin SEO — generar una mínima
    if not desc:
        desc = (f"{title}\n\nUn recorrido completo sobre {niche}. "
                f"En este vídeo analizamos en profundidad esta historia con todos los detalles.\n\n"
                f"📌 SUSCRÍBETE para más contenido sobre {niche}.")
    if not hashtags:
        base = niche.replace(' ', '')
        hashtags = f"#{base} #documental #historia #YouTube #viral"

    # Combinar descripción con capítulos (YouTube los indexa automáticamente)
    parts = [desc]
    if chapters:
        parts.append(f"📖 CONTENIDO DEL VÍDEO:\n{chapters}")
    parts.append(hashtags)
    full_description = "\n\n".join(parts)
    tags_list = _sanitize_tags(tags_raw)

    return {
        "description": full_description,
        "hashtags": hashtags,
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
            "categoryId": "22",
            "defaultLanguage": "es",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
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
