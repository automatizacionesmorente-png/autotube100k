import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from .database import (
    init_db, create_job, update_job, get_job, get_jobs,
    get_finance_summary, get_channels, upsert_channel, add_step
)

app = FastAPI(title="AutoTube API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "./output"))
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Progreso en memoria para SSE
_job_events: dict[str, list] = {}
# Jobs cancelados (para que el pipeline los detecte y pare)
_cancelled_jobs: set[str] = set()

@app.on_event("startup")
async def startup():
    init_db()
    OUTPUT_DIR.mkdir(exist_ok=True)
    _sweep_orphan_temp()


def _sweep_orphan_temp():
    """Borra carpetas temporales huérfanas (de vídeos que fallaron a medias)."""
    import shutil
    try:
        for pattern in ("*/tmp_*", "*/_xtts_tmp", "*/_short_tmp"):
            for d in OUTPUT_DIR.glob(pattern):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

# ── Frontend ───────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def root():
    return FileResponse(FRONTEND_DIR / "index.html")

# ── Modelos ────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    niche: str
    title: str
    tone: str = "neutro"
    channel_id: str | None = None
    context: str | None = None  # datos verificados para temas de actualidad (el guion NO inventa)
    use_custom_voice: bool = False  # usar voz clonada subida (voices/custom.wav)

class ChannelRequest(BaseModel):
    name: str
    niche: str | None = None

# ── Endpoints de generación ────────────────────────────────────────
@app.post("/api/generate")
async def start_generation(req: GenerateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    create_job(job_id, req.niche, req.title, req.tone, req.channel_id)
    _job_events[job_id] = []
    background_tasks.add_task(run_pipeline, job_id, req)
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    async def event_generator() -> AsyncGenerator:
        sent = 0
        while True:
            events = _job_events.get(job_id, [])
            while sent < len(events):
                yield {"data": json.dumps(events[sent])}
                sent += 1
            job = get_job(job_id)
            if job and job["status"] in ("done", "error"):
                yield {"data": json.dumps({"type": "end", "job": job})}
                break
            await asyncio.sleep(1)
    return EventSourceResponse(event_generator())

@app.get("/api/jobs")
async def list_jobs():
    return get_jobs()

@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return job

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    _cancelled_jobs.add(job_id)
    update_job(job_id, status="cancelled", error="Cancelado por el usuario")
    _job_events.setdefault(job_id, []).append(
        {"type": "error", "message": "Producción cancelada por el usuario"}
    )
    return {"ok": True}

@app.get("/api/jobs/{job_id}/video")
async def stream_video(job_id: str):
    """Sirve el vídeo final con soporte completo de range requests para streaming."""
    from fastapi import Request
    from fastapi.responses import StreamingResponse
    import aiofiles

    job = get_job(job_id)
    if not job or not job.get("video_path"):
        raise HTTPException(404, "Vídeo no disponible aún")
    video_path = Path(job["video_path"])
    if not video_path.exists():
        raise HTTPException(404, "Archivo de vídeo no encontrado en disco")
    return FileResponse(
        video_path,
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        }
    )

class VoiceUpload(BaseModel):
    audio: str  # data URL base64 de un audio (mp3/wav/m4a) con la voz a clonar

VOICES_DIR_SRV = Path("/root/autotube100k/voices")

@app.post("/api/voice-sample")
async def upload_voice_sample(req: VoiceUpload):
    """Sube una muestra de voz real, la limpia y la prepara para clonar con XTTS.
    Genera además un clip de prueba de 6s para escuchar el clon. Gratis."""
    import base64, subprocess, tempfile
    data = req.audio
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception:
        raise HTTPException(400, "Audio base64 inválido")
    if len(raw) < 2000:
        raise HTTPException(400, "Audio demasiado corto o vacío")

    VOICES_DIR_SRV.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mktemp(suffix=".bin"))
    tmp.write_bytes(raw)
    ref = VOICES_DIR_SRV / "custom.wav"
    # Convertir: mono, 22050Hz, primeros 30s, filtro de ruido suave (formato ideal XTTS)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp), "-t", "30",
             "-af", "highpass=f=70,lowpass=f=8000,afftdn=nf=-25,loudnorm=I=-18",
             "-ac", "1", "-ar", "22050", str(ref)],
            check=True, capture_output=True, timeout=60
        )
    except Exception as e:
        raise HTTPException(400, f"No se pudo procesar el audio: {str(e)[:120]}")
    finally:
        tmp.unlink(missing_ok=True)

    # Clip de prueba de 6s con la voz clonada (para que el usuario la escuche ya)
    test_url = None
    try:
        from .pipeline.tts import _xtts_available, _xtts_generate, _postprocess_audio
        if _xtts_available():
            test_dir = OUTPUT_DIR / "_voicetest"
            test_dir.mkdir(parents=True, exist_ok=True)
            raw_mp3 = test_dir / "test.raw.mp3"
            out_mp3 = test_dir / "voicetest.mp3"
            await asyncio.to_thread(
                _xtts_generate,
                "Hola, esta es una prueba de mi voz clonada para el canal. Suena natural y profesional.",
                raw_mp3, str(ref)
            )
            await asyncio.to_thread(_postprocess_audio, raw_mp3, out_mp3, "neutro")
            raw_mp3.unlink(missing_ok=True)
            test_url = "/api/voice-sample/test"
    except Exception:
        pass
    return {"ok": True, "test_url": test_url}

@app.get("/api/voice-sample/test")
async def voice_sample_test():
    p = OUTPUT_DIR / "_voicetest" / "voicetest.mp3"
    if not p.exists():
        raise HTTPException(404, "Sin clip de prueba")
    return FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})

class ThumbnailUpload(BaseModel):
    image: str  # data URL base64 (data:image/...;base64,XXXX) o base64 puro

@app.post("/api/jobs/{job_id}/thumbnail")
async def upload_custom_thumbnail(job_id: str, req: ThumbnailUpload):
    """Sube una miniatura propia (hecha externamente). Se guarda como custom_thumbnail.jpg
    y tiene prioridad sobre la auto-generada al subir a YouTube."""
    import base64, io
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    data = req.image
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]  # quitar prefijo data:image/...;base64,
    try:
        raw = base64.b64decode(data)
    except Exception:
        raise HTTPException(400, "Imagen base64 inválida")
    if len(raw) < 1000:
        raise HTTPException(400, "Imagen demasiado pequeña o vacía")
    # Normalizar a JPG 1280x720 con PIL (YouTube recomienda 1280x720, <2MB)
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = img.size
        target = 1280 / 720
        if w / h > target:  # demasiado ancha → recortar lados
            nw = int(h * target); img = img.crop(((w - nw)//2, 0, (w - nw)//2 + nw, h))
        else:                # demasiado alta → recortar arriba/abajo
            nh = int(w / target); img = img.crop((0, (h - nh)//2, w, (h - nh)//2 + nh))
        img = img.resize((1280, 720), Image.LANCZOS)
        img.save(job_dir / "custom_thumbnail.jpg", "JPEG", quality=92)
    except Exception as e:
        raise HTTPException(400, f"No se pudo procesar la imagen: {e}")
    return {"ok": True, "file": "custom_thumbnail.jpg"}

@app.get("/api/jobs/{job_id}/file/{filename}")
async def serve_job_file(job_id: str, filename: str):
    """Sirve cualquier archivo del job (miniaturas, short, etc.)."""
    import re
    if not re.match(r'^[\w\-\.]+$', filename):
        raise HTTPException(400, "Nombre de archivo inválido")
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    job_dir = OUTPUT_DIR / job_id
    file_path = job_dir / filename
    if not file_path.exists():
        raise HTTPException(404, f"{filename} no encontrado")
    mt = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"
    return FileResponse(file_path, media_type=mt,
                        headers={"Cache-Control": "no-cache"})


class UploadRequest(BaseModel):
    channel_id: str | None = None
    thumbnail: str | None = None  # variante de miniatura elegida (A/B/C)

@app.post("/api/jobs/{job_id}/upload")
async def upload_job_to_youtube(job_id: str, req: UploadRequest, background_tasks: BackgroundTasks):
    """Sube manualmente el vídeo a YouTube tras revisión."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    if not job.get("video_path"):
        raise HTTPException(400, "El vídeo aún no está listo")
    if job.get("youtube_url"):
        return {"youtube_url": job["youtube_url"], "already_uploaded": True}
    if not req.channel_id:
        raise HTTPException(400, "Selecciona un canal de YouTube conectado primero. Ve a la pestaña Canales y conecta tu canal con OAuth.")

    background_tasks.add_task(_do_upload, job_id, req.channel_id, req.thumbnail)
    return {"ok": True, "message": "Subiendo a YouTube…"}

async def _do_upload(job_id: str, channel_id: str, thumbnail: str = None):
    job = get_job(job_id)
    try:
        import json as _json
        from .pipeline.upload import generate_metadata, upload_to_youtube
        title  = job.get("title", "")
        niche  = job.get("niche", "")
        # Intentar usar metadata.json guardado durante la generación (más completo)
        meta_path = Path(job["video_path"]).parent / "metadata.json"
        if meta_path.exists():
            saved = _json.loads(meta_path.read_text())
            metadata = {k: saved[k] for k in ("description", "hashtags", "tags") if k in saved}
        else:
            script = job.get("script", "")
            from .pipeline.render import get_duration
            try:
                dur = get_duration(Path(job["video_path"]))
            except Exception:
                dur = None
            metadata = await asyncio.to_thread(generate_metadata, job_id, script, title, niche, dur)
        # Miniatura: prioridad a la PROPIA del usuario (custom_thumbnail.jpg) si existe.
        import re as _re
        job_d = Path(job["video_path"]).parent
        thumb_name = thumbnail if (thumbnail and _re.match(r'^[\w\-\.]+$', thumbnail)) else None
        custom = job_d / "custom_thumbnail.jpg"
        if thumb_name:
            thumb_path = job_d / thumb_name
        elif custom.exists():
            thumb_path = custom            # sin selección explícita → usar la propia si existe
        else:
            thumb_path = job_d / "thumbnail.jpg"
        if not thumb_path.exists():
            thumb_path = custom if custom.exists() else (job_d / "thumbnail.jpg")
        result = await asyncio.to_thread(
            upload_to_youtube, job_id, Path(job["video_path"]),
            thumb_path,
            title, metadata, channel_id
        )
        youtube_url, studio_url = result if isinstance(result, tuple) else (result, None)
        from datetime import datetime
        update_job(job_id, youtube_url=youtube_url, finished_at=datetime.utcnow().isoformat())
        _job_events.setdefault(job_id, []).append(
            {"type": "uploaded", "youtube_url": youtube_url, "studio_url": studio_url}
        )
        # Limpieza post-subida: el vídeo ya está en YouTube, liberar disco
        if youtube_url:
            try:
                from .pipeline.render import cleanup_after_upload
                cleanup_after_upload(Path(job["video_path"]).parent)
            except Exception:
                pass
    except Exception as e:
        update_job(job_id, error=f"Upload failed: {e}")

@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    from .database import get_conn
    conn = get_conn()
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.execute("DELETE FROM job_steps WHERE job_id=?", (job_id,))
    conn.execute("DELETE FROM cost_events WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ── Finance ────────────────────────────────────────────────────────
@app.get("/api/finance")
async def finance():
    return get_finance_summary()

# ── Canales ────────────────────────────────────────────────────────
@app.get("/api/channels")
async def channels():
    return get_channels()

@app.post("/api/channels")
async def add_channel(req: ChannelRequest):
    channel_id = str(uuid.uuid4())[:8]
    upsert_channel(channel_id, req.name, req.niche)
    return {"id": channel_id, "name": req.name}

@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str):
    from .database import get_conn
    conn = get_conn()
    conn.execute("DELETE FROM channels WHERE id=?", (channel_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/channels/{channel_id}/detail")
async def channel_detail_stats(channel_id: str):
    """Devuelve stats completas + últimos vídeos de un canal desde YouTube API."""
    from .database import get_conn
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    conn = get_conn()
    ch = conn.execute(
        "SELECT * FROM channels WHERE id=?", (channel_id,)
    ).fetchone()
    conn.close()

    if not ch:
        raise HTTPException(404, "Canal no encontrado")
    ch = dict(ch)
    if not ch.get("connected"):
        return {"channel": ch, "stats": None, "videos": []}

    try:
        creds = Credentials(
            token=ch["access_token"],
            refresh_token=ch["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ.get("YOUTUBE_CLIENT_ID"),
            client_secret=os.environ.get("YOUTUBE_CLIENT_SECRET"),
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            conn2 = get_conn()
            conn2.execute("UPDATE channels SET access_token=? WHERE id=?", (creds.token, channel_id))
            conn2.commit()
            conn2.close()

        yt = build("youtube", "v3", credentials=creds)

        # Stats del canal
        ch_resp = yt.channels().list(
            part="statistics,snippet,brandingSettings", mine=True
        ).execute()
        yt_channel_id = None
        stats = {}
        snippet = {}
        if ch_resp.get("items"):
            item = ch_resp["items"][0]
            yt_channel_id = item["id"]
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})

        # Últimos 10 vídeos
        videos = []
        if yt_channel_id:
            search_resp = yt.search().list(
                part="snippet", channelId=yt_channel_id,
                order="date", maxResults=10, type="video"
            ).execute()
            video_ids = [i["id"]["videoId"] for i in search_resp.get("items", [])]
            if video_ids:
                vids_resp = yt.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(video_ids)
                ).execute()
                for v in vids_resp.get("items", []):
                    vs = v.get("statistics", {})
                    videos.append({
                        "id": v["id"],
                        "title": v["snippet"]["title"],
                        "thumbnail": v["snippet"]["thumbnails"].get("medium", {}).get("url"),
                        "published": v["snippet"]["publishedAt"][:10],
                        "views": int(vs.get("viewCount", 0)),
                        "likes": int(vs.get("likeCount", 0)),
                        "comments": int(vs.get("commentCount", 0)),
                        "duration": v.get("contentDetails", {}).get("duration", ""),
                    })

        # Sumar views de los vídeos individuales (más fresco que el contador del canal)
        real_views = sum(v["views"] for v in videos) if videos else int(stats.get("viewCount", 0))
        return {
            "channel": ch,
            "stats": {
                "subscribers": int(stats.get("subscriberCount", 0)),
                "total_views": real_views,
                "videos_count": int(stats.get("videoCount", 0)),
                "description": snippet.get("description", ""),
                "country": snippet.get("country", ""),
                "created": snippet.get("publishedAt", "")[:10] if snippet.get("publishedAt") else "",
            },
            "videos": videos,
        }
    except Exception as e:
        import traceback
        return {"channel": ch, "stats": None, "videos": [], "error": str(e), "trace": traceback.format_exc()}

@app.get("/api/channels/sync")
async def sync_channels_stats():
    """Sincroniza subs y vídeos de todos los canales conectados desde YouTube API."""
    from .database import get_conn
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    conn = get_conn()
    channels = conn.execute(
        "SELECT id, name, access_token, refresh_token, youtube_channel_id FROM channels WHERE connected=1"
    ).fetchall()

    updated = []
    for ch in channels:
        try:
            creds = Credentials(
                token=ch["access_token"],
                refresh_token=ch["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.environ.get("YOUTUBE_CLIENT_ID"),
                client_secret=os.environ.get("YOUTUBE_CLIENT_SECRET"),
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                conn.execute(
                    "UPDATE channels SET access_token=? WHERE id=?",
                    (creds.token, ch["id"])
                )

            yt = build("youtube", "v3", credentials=creds)
            resp = yt.channels().list(part="statistics,snippet", mine=True).execute()
            if resp.get("items"):
                stats = resp["items"][0]["statistics"]
                subs = int(stats.get("subscriberCount", 0))
                vids = int(stats.get("videoCount", 0))
                views = int(stats.get("viewCount", 0))
                conn.execute(
                    "UPDATE channels SET subscribers=?, videos_count=?, total_views=? WHERE id=?",
                    (subs, vids, views, ch["id"])
                )
                updated.append({"id": ch["id"], "name": ch["name"], "subscribers": subs, "videos": vids, "views": views})
        except Exception as e:
            updated.append({"id": ch["id"], "name": ch["name"], "error": str(e)})

    conn.commit()
    conn.close()
    return {"synced": updated}

YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

def _yt_flow():
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("YOUTUBE_CLIENT_ID", ""),
                "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
                "redirect_uris": [os.environ.get("YOUTUBE_REDIRECT_URI", "")],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=YT_SCOPES,
    )

@app.get("/api/youtube/connect")
async def youtube_connect():
    """Crea canal temporal e inicia OAuth — el nombre se detecta automáticamente."""
    from .database import get_conn
    temp_id = str(uuid.uuid4())[:8]
    conn = get_conn()
    conn.execute("INSERT INTO channels (id, name, connected) VALUES (?, ?, 0)", (temp_id, "Conectando…"))
    conn.commit()
    conn.close()
    flow = _yt_flow()
    flow.redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI")
    auth_url, _ = flow.authorization_url(state=temp_id, access_type="offline", prompt="consent")
    return {"auth_url": auth_url}

@app.get("/api/youtube/auth/{channel_id}")
async def youtube_auth(channel_id: str):
    """Reconecta un canal existente."""
    flow = _yt_flow()
    flow.redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI")
    auth_url, _ = flow.authorization_url(state=channel_id, access_type="offline", prompt="consent")
    return {"auth_url": auth_url}

@app.get("/api/youtube/callback")
async def youtube_callback(code: str, state: str):
    from .database import get_conn
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    flow = _yt_flow()
    flow.redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI")
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Detectar nombre real del canal desde YouTube API
    channel_name = "Mi Canal"
    yt_channel_id = None
    try:
        yt = build("youtube", "v3", credentials=creds)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        if resp.get("items"):
            channel_name = resp["items"][0]["snippet"]["title"]
            yt_channel_id = resp["items"][0]["id"]
    except Exception:
        pass

    conn = get_conn()
    conn.execute(
        """UPDATE channels
           SET name=?, youtube_channel_id=?, access_token=?, refresh_token=?, connected=1
           WHERE id=?""",
        (channel_name, yt_channel_id, creds.token, creds.refresh_token, state)
    )
    conn.commit()
    conn.close()

    # Redirige al dashboard con el canal ya conectado
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <html><head>
    <meta http-equiv="refresh" content="0;url=https://autotube100k.vercel.app/#canales">
    </head><body>
    <script>
      if (window.opener) { window.opener.postMessage('yt_connected','*'); window.close(); }
      else { window.location.href='https://autotube100k.vercel.app/#canales'; }
    </script>
    </body></html>
    """)

# ── Pipeline principal ────────────────────────────────────────────
async def run_pipeline(job_id: str, req: GenerateRequest):
    def emit(step: str, status: str, message: str, progress: int, cost: float = 0):
        # Rastrear paso actual para marcar como error si falla
        _current_step["name"] = step
        # Leer coste real acumulado de la DB en lugar de usar valor hardcodeado
        job_data = get_job(job_id)
        real_cost = job_data["cost_total"] if job_data else cost
        event = {"type": "step", "step": step, "status": status,
                 "message": message, "progress": progress, "cost": real_cost}
        _job_events.setdefault(job_id, []).append(event)
        update_job(job_id, current_step=step, progress=progress)

    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    _current_step = {"name": "script"}  # rastrear paso actual para marcar error

    def check_cancelled():
        if job_id in _cancelled_jobs:
            raise RuntimeError("Cancelado por el usuario")

    try:
        update_job(job_id, status="running")

        # ── Paso 1: Guión ──────────────────────────────────────────
        emit("script", "running", "Generando guión con Claude Sonnet 4.6…", 5)
        from .pipeline.script_gen import generate_script
        script = await asyncio.to_thread(generate_script, job_id, req.niche, req.title, req.tone, req.context)
        update_job(job_id, script=script[:8000])  # guardar más para metadata de calidad
        wcount = len(script.split())
        emit("script", "done", f"Guión listo: {wcount} palabras (~{wcount//130} min)", 18)
        check_cancelled()

        # ── Pasos 2+3 en PARALELO: TTS + prompts de imagen ─────────
        emit("tts", "running", "Convirtiendo a voz neural expresiva (Edge TTS + SSML)…", 20)
        emit("images", "running", "Generando prompts de imagen con IA…", 20)
        from .pipeline.tts import generate_audio
        from .pipeline.images import (generate_image_prompts, generate_images,
                                       generate_hook_images, generate_thumbnail)
        audio_path = job_dir / "narration.mp3"
        custom_voice_ref = None
        if getattr(req, "use_custom_voice", False):
            _cv = Path("/root/autotube100k/voices/custom.wav")
            if _cv.exists():
                custom_voice_ref = str(_cv)
        audio_task   = asyncio.to_thread(generate_audio, job_id, script, req.tone, audio_path, custom_voice_ref)
        prompts_task = asyncio.to_thread(generate_image_prompts, job_id, script, req.niche, 40)
        audio_result, img_prompts = await asyncio.gather(audio_task, prompts_task)
        emit("tts", "done", "Audio con pausas dramáticas listo", 35)
        check_cancelled()

        # ── Pasos 4+5+6 en PARALELO: imágenes cuerpo + hook + thumbnail ──
        images_dir   = job_dir / "images"
        hook_imgs_dir = job_dir / "hook_images"

        def on_img_progress(done: int, total: int, fal_cost: float):
            pct = 40 + int(done / total * 15)
            _job_events.setdefault(job_id, []).append({
                "type": "step", "step": "images", "status": "running",
                "message": f"Imagen {done}/{total} · fal.ai acumulado: {fal_cost:.4f}€",
                "progress": pct, "cost": fal_cost, "realtime": True,
            })

        emit("images", "running", "Generando 60 imágenes + 8 hook + B-roll vídeo + thumbnail en paralelo…", 37)
        from .pipeline.hook_videos import (generate_hook_prompts as gen_hookvid_prompts,
                                           generate_hook_videos, generate_body_videos)
        hook_vids_dir = job_dir / "hook_videos"

        def _hook_videos_task():
            # Footage real de Pexels para el hook (gratis). Si no hay key, devuelve [].
            queries = gen_hookvid_prompts(job_id, script, req.niche, 5)
            return generate_hook_videos(job_id, queries, hook_vids_dir)

        body_task  = asyncio.to_thread(generate_images, job_id, img_prompts, images_dir, on_img_progress)
        hook_task  = asyncio.to_thread(generate_hook_images, job_id, req.niche, script[:800], hook_imgs_dir)
        thumb_task = asyncio.to_thread(generate_thumbnail, job_id, req.title, req.niche, job_dir, req.tone)
        hookvid_task = asyncio.to_thread(_hook_videos_task)
        bodyvid_task = asyncio.to_thread(generate_body_videos, job_id, script, req.niche, 35)
        body_images, hook_images, thumbnail_path, hook_clips, body_clips = await asyncio.gather(
            body_task, hook_task, thumb_task, hookvid_task, bodyvid_task
        )

        emit("images",    "done", f"{len(body_images)} imágenes + {len(body_clips)} clips vídeo (cuerpo dinámico)", 60)
        hook_src = f"{len(hook_clips)} clips vídeo Pexels" if hook_clips else f"{len(hook_images)} imágenes"
        emit("hook_videos","done", f"Hook: {hook_src}", 62)
        emit("thumbnail", "done", "Miniatura con título lista", 64)
        check_cancelled()

        # ── Paso 7: Render FFmpeg ─────────────────────────────────
        emit("render", "running", "Montando vídeo con FFmpeg (sin huecos en blanco)…", 66)
        from .pipeline.render import render_video
        final_path = job_dir / "final.mp4"
        await asyncio.to_thread(
            render_video, job_id,
            hook_clips,
            body_images, audio_path, final_path, req.title,
            hook_images, req.tone, body_clips
        )
        emit("render", "done", "Vídeo montado completamente", 90)

        # Limpieza post-render: borrar archivos de trabajo (el vídeo ya los contiene)
        try:
            from .pipeline.render import cleanup_intermediates
            freed = cleanup_intermediates(job_dir)
            if freed > 5:
                emit("render", "done", f"Vídeo montado · {freed:.0f}MB de temporales liberados", 90)
        except Exception:
            pass

        # ── Paso 8: Metadata ──────────────────────────────────────
        emit("upload", "running", "Generando descripción y capítulos sincronizados...", 92)
        from .pipeline.upload import generate_metadata
        from .pipeline.render import get_duration
        try:
            audio_dur = get_duration(final_path)  # final.mp4 (narration.mp3 ya borrado)
        except Exception:
            audio_dur = None
        metadata = await asyncio.to_thread(generate_metadata, job_id, script, req.title, req.niche, audio_dur)

        # Guardar metadatos en archivo para la subida manual posterior
        import json as _json
        meta_path = job_dir / "metadata.json"
        meta_path.write_text(_json.dumps({
            "title": req.title, "niche": req.niche,
            "channel_id": req.channel_id, **metadata
        }, ensure_ascii=False))

        emit("upload", "done", "✅ Vídeo listo — revísalo antes de subir a YouTube", 100, 0)

        from datetime import datetime
        update_job(
            job_id, status="done", progress=100,
            video_path=str(final_path),
            youtube_url=None,
            finished_at=datetime.utcnow().isoformat()
        )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        error_msg = str(e)
        update_job(job_id, status="error", error=error_msg)
        # Marcar el paso actual como fallido (aparece en rojo en la UI)
        job_data = get_job(job_id)
        real_cost = job_data["cost_total"] if job_data else 0
        _job_events.setdefault(job_id, []).append({
            "type": "step", "step": _current_step["name"], "status": "error",
            "message": f"❌ Error: {error_msg[:120]}", "progress": 0, "cost": real_cost
        })
        _job_events.setdefault(job_id, []).append({
            "type": "error", "message": error_msg, "detail": tb
        })
