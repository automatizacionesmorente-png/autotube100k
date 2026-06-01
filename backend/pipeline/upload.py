import os
import anthropic
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from ..database import add_step, get_conn

def generate_metadata(job_id: str, script: str, title: str, niche: str) -> dict:
    """Haiku genera descripción y hashtags."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": f"""Título: {title}
Nicho: {niche}

Escribe:
1. DESCRIPCION: (2-3 párrafos atractivos para YouTube, incluye palabras clave naturalmente)
2. HASHTAGS: (10-15 hashtags relevantes separados por espacio, sin salto de línea)

Usa exactamente esas etiquetas."""
        }]
    )
    text = msg.content[0].text
    desc = _extract_between(text, "DESCRIPCION:", "HASHTAGS:").strip()
    hashtags = _extract_after(text, "HASHTAGS:").strip()
    return {"description": desc, "hashtags": hashtags}

def upload_to_youtube(
    job_id: str,
    video_path: Path,
    thumbnail_path: Path,
    title: str,
    metadata: dict,
    channel_id: str,
) -> str:
    add_step(job_id, "upload", "running", "Subiendo vídeo a YouTube")

    creds = _get_channel_credentials(channel_id)
    if not creds:
        add_step(job_id, "upload", "skipped", "Canal sin credenciales YouTube configuradas")
        return None

    youtube = build("youtube", "v3", credentials=creds)

    description = metadata["description"] + "\n\n" + metadata["hashtags"]
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": [t.lstrip("#") for t in metadata["hashtags"].split()[:15]],
            "categoryId": "22",  # People & Blogs
            "defaultLanguage": "es",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        }
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=10*1024*1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response["id"]

    # Subir thumbnail
    if thumbnail_path and thumbnail_path.exists():
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")
        ).execute()

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    add_step(job_id, "upload", "done", f"Subido: {youtube_url}", 0)
    return youtube_url

def _get_channel_credentials(channel_id: str) -> Credentials | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT access_token, refresh_token FROM channels WHERE id = ? AND connected = 1",
        (channel_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("YOUTUBE_CLIENT_ID"),
        client_secret=os.environ.get("YOUTUBE_CLIENT_SECRET"),
    )

def _extract_between(text, start, end):
    try:
        s = text.index(start) + len(start)
        e = text.index(end)
        return text[s:e]
    except ValueError:
        return text

def _extract_after(text, tag):
    try:
        return text[text.index(tag) + len(tag):]
    except ValueError:
        return ""
