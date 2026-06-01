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

@app.on_event("startup")
async def startup():
    init_db()
    OUTPUT_DIR.mkdir(exist_ok=True)

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

@app.get("/api/youtube/auth/{channel_id}")
async def youtube_auth(channel_id: str):
    """Inicia el flujo OAuth2 de YouTube para un canal."""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("YOUTUBE_CLIENT_ID", ""),
                "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
                "redirect_uris": [os.environ.get("YOUTUBE_REDIRECT_URI", "http://localhost:8000/api/youtube/callback")],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    flow.redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI")
    auth_url, _ = flow.authorization_url(state=channel_id, access_type="offline", prompt="consent")
    return {"auth_url": auth_url}

@app.get("/api/youtube/callback")
async def youtube_callback(code: str, state: str):
    from google_auth_oauthlib.flow import Flow
    from .database import get_conn
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("YOUTUBE_CLIENT_ID", ""),
                "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
                "redirect_uris": [os.environ.get("YOUTUBE_REDIRECT_URI", "")],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    flow.redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI")
    flow.fetch_token(code=code)
    creds = flow.credentials
    conn = get_conn()
    conn.execute(
        "UPDATE channels SET access_token=?, refresh_token=?, connected=1 WHERE id=?",
        (creds.token, creds.refresh_token, state)
    )
    conn.commit()
    conn.close()
    return FileResponse(FRONTEND_DIR / "index.html")

# ── Pipeline principal ────────────────────────────────────────────
async def run_pipeline(job_id: str, req: GenerateRequest):
    def emit(step: str, status: str, message: str, progress: int, cost: float = 0):
        event = {"type": "step", "step": step, "status": status,
                 "message": message, "progress": progress, "cost": cost}
        _job_events.setdefault(job_id, []).append(event)
        update_job(job_id, current_step=step, progress=progress)

    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        update_job(job_id, status="running")

        # ── Paso 1: Guión ──────────────────────────────────────────
        emit("script", "running", "Generando guión con Claude Sonnet...", 5)
        from .pipeline.script_gen import generate_script
        script = await asyncio.to_thread(generate_script, job_id, req.niche, req.title, req.tone)
        update_job(job_id, script=script[:500])
        emit("script", "done", f"Guión listo: {len(script.split())} palabras", 20, 0.07)

        # ── Paso 2: Voz ───────────────────────────────────────────
        emit("tts", "running", "Convirtiendo a voz con Kokoro TTS...", 25)
        from .pipeline.tts import generate_audio
        audio_path = job_dir / "narration.mp3"
        await asyncio.to_thread(generate_audio, job_id, script, req.tone, audio_path)
        emit("tts", "done", "Audio generado", 40, 0.0)

        # ── Paso 3: Prompts de imágenes ────────────────────────────
        emit("images", "running", "Analizando guión para prompts de imagen...", 42)
        from .pipeline.images import generate_image_prompts, generate_images, generate_thumbnail
        img_prompts = await asyncio.to_thread(generate_image_prompts, job_id, script, req.niche, 15)

        # ── Paso 4: Imágenes Flux Schnell ──────────────────────────
        emit("images", "running", f"Generando {len(img_prompts)} imágenes con Flux Schnell...", 45)
        images_dir = job_dir / "images"
        body_images = await asyncio.to_thread(generate_images, job_id, img_prompts, images_dir)
        emit("images", "done", f"{len(body_images)} imágenes generadas", 60, len(body_images) * 0.003 * 0.92)

        # ── Paso 5: Thumbnail ──────────────────────────────────────
        emit("thumbnail", "running", "Generando miniatura...", 62)
        thumbnail_path = await asyncio.to_thread(generate_thumbnail, job_id, req.title, req.niche, job_dir)
        emit("thumbnail", "done", "Miniatura lista", 65, 0.003 * 0.92)

        # ── Paso 6: Vídeos hook Kling ──────────────────────────────
        emit("hook_videos", "running", "Generando 5 vídeos hook con Kling Standard...", 65)
        from .pipeline.hook_videos import generate_hook_prompts, generate_hook_videos
        hook_prompts = await asyncio.to_thread(generate_hook_prompts, job_id, script, req.niche, 5)
        videos_dir = job_dir / "hook_videos"
        hook_videos = await asyncio.to_thread(generate_hook_videos, job_id, hook_prompts, videos_dir)
        emit("hook_videos", "done", f"{len(hook_videos)} vídeos hook listos", 80, len(hook_videos) * 0.14 * 0.92)

        # ── Paso 7: Render FFmpeg ──────────────────────────────────
        emit("render", "running", "Montando vídeo con FFmpeg...", 82)
        from .pipeline.render import render_video
        final_path = job_dir / "final.mp4"
        await asyncio.to_thread(render_video, job_id, hook_videos, body_images, audio_path, final_path, req.title)
        emit("render", "done", "Vídeo montado", 90, 0)

        # ── Paso 8: Metadata + subida YouTube ─────────────────────
        emit("upload", "running", "Generando descripción y subiendo a YouTube...", 92)
        from .pipeline.upload import generate_metadata, upload_to_youtube
        metadata = await asyncio.to_thread(generate_metadata, job_id, script, req.title, req.niche)

        youtube_url = None
        if req.channel_id:
            youtube_url = await asyncio.to_thread(
                upload_to_youtube, job_id, final_path, thumbnail_path,
                req.title, metadata, req.channel_id
            )

        emit("upload", "done", youtube_url or "Vídeo listo (sin canal conectado)", 100, 0)

        from datetime import datetime
        update_job(
            job_id, status="done", progress=100,
            video_path=str(final_path),
            youtube_url=youtube_url,
            finished_at=datetime.utcnow().isoformat()
        )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        update_job(job_id, status="error", error=str(e))
        _job_events.setdefault(job_id, []).append({
            "type": "error", "message": str(e), "detail": tb
        })
