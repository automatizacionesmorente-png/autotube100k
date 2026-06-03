"""
Hook videos — Pexels stock footage (GRATIS, sin derechos de autor).

Pexels ofrece miles de clips de vídeo profesionales libres de derechos.
Registrarse en pexels.com/api (gratis) y añadir PEXELS_API_KEY al .env.

Sin PEXELS_API_KEY → el render usa las 8 imágenes hook generadas con Flux.
"""
import os
import re
import time
import httpx
import anthropic
from pathlib import Path
from ..database import add_cost_event, add_step

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"


def generate_hook_prompts(job_id: str, script: str, niche: str, count: int = 5) -> list[str]:
    """Extrae 5 queries en inglés para buscar en Pexels."""
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": f"""Del siguiente inicio de guión sobre '{niche}',
extrae {count} queries de búsqueda en inglés para buscar clips de vídeo en Pexels.
Deben ser palabras clave simples que devuelvan clips cinematográficos relevantes.
Ejemplos: "ancient ruins dramatic", "dark forest fog", "city night rain", "old documents secret"
SOLO las queries, una por línea, sin numeración.

GUIÓN:
{script[:800]}"""}]
        )
        queries = [q.strip() for q in msg.content[0].text.strip().split("\n") if q.strip()][:count]
        cost = (msg.usage.input_tokens * 0.8 + msg.usage.output_tokens * 4) / 1_000_000 * 0.92
        add_cost_event(job_id, "claude_haiku_hook", msg.usage.output_tokens, 4/1_000_000, cost)
        return queries
    except Exception:
        return [niche, "mystery dark", "dramatic reveal", "ancient secret", "cinematic night"]


def generate_body_videos(job_id: str, script: str, niche: str, count: int = 35) -> list[Path]:
    """
    Descarga ~count clips de vídeo real de Pexels (GRATIS) repartidos por todo
    el guion, para intercalar con las imágenes en el cuerpo y dar movimiento real.
    Devuelve [] si no hay PEXELS_API_KEY.
    """
    from pathlib import Path as _P
    pexels_key = os.environ.get("PEXELS_API_KEY", "")
    if not pexels_key:
        add_step(job_id, "body_videos", "done", "Sin PEXELS_API_KEY — cuerpo solo con imágenes (0€)", 0)
        return []

    # Dividir el guion en `count` segmentos y generar una query por segmento (1 sola llamada Haiku)
    words = script.split()
    seg = max(1, len(words) // count)
    segments = [" ".join(words[i*seg:(i+1)*seg])[:200] for i in range(count)]
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        seg_text = "\n".join(f"[{i+1}] {s}" for i, s in enumerate(segments))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": f"""Para cada fragmento de guion sobre '{niche}', dame UNA query corta en inglés (2-4 palabras) para buscar un clip de vídeo cinematográfico relevante en Pexels.
Ejemplos: "nuclear reactor", "soviet city night", "fire smoke dramatic", "old documents".
Devuelve SOLO {count} queries, una por línea, sin numeración.

FRAGMENTOS:
{seg_text}"""}]
        )
        queries = [q.strip() for q in msg.content[0].text.strip().split("\n") if q.strip()][:count]
        cost = (msg.usage.input_tokens * 0.8 + msg.usage.output_tokens * 4) / 1_000_000 * 0.92
        add_cost_event(job_id, "claude_haiku_hook", msg.usage.output_tokens, 4/1_000_000, cost)
    except Exception:
        queries = [niche, "cinematic dramatic", "dark history", "old archive"] * (count // 4 + 1)
    queries = queries[:count]

    add_step(job_id, "body_videos", "running",
             f"Descargando {len(queries)} clips de vídeo real de Pexels (gratis, sin derechos)…")
    out_dir = OUTPUT_BODY_DIR(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()

    def _one(i, query):
        try:
            with httpx.Client(timeout=60) as client:
                p = _fetch_pexels_clip(client, pexels_key, query, out_dir, i)
            if p:
                with lock:
                    paths.append(p)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda t: _one(*t), enumerate(queries)))

    add_step(job_id, "body_videos", "done",
             f"{len(paths)} clips de vídeo Pexels para el cuerpo (0€)", 0)
    return paths


def OUTPUT_BODY_DIR(job_id: str):
    from pathlib import Path as _P
    return _P(__file__).parent.parent.parent / "output" / job_id / "body_videos"


def generate_hook_videos(job_id: str, prompts: list[str], output_dir: Path) -> list[Path]:
    """
    Descarga clips de Pexels (gratis) para el hook.
    Si no hay PEXELS_API_KEY, devuelve [] y el render usa imágenes hook.
    """
    pexels_key = os.environ.get("PEXELS_API_KEY", "")

    if not pexels_key:
        add_step(job_id, "hook_videos", "done",
                 "Sin PEXELS_API_KEY — hook usará imágenes cinematic (0€)", 0)
        return []

    add_step(job_id, "hook_videos", "running",
             f"Descargando {len(prompts)} clips de Pexels (gratis, sin derechos)…")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    with httpx.Client(timeout=60) as client:
        for i, query in enumerate(prompts):
            try:
                path = _fetch_pexels_clip(client, pexels_key, query, output_dir, i)
                if path:
                    paths.append(path)
            except Exception as e:
                add_step(job_id, "hook_videos", "running",
                         f"Clip {i+1} falló ({str(e)[:50]}), continuando…")

    count = len(paths)
    add_step(job_id, "hook_videos", "done",
             f"{'✅' if count >= 3 else '⚠️'} {count}/{len(prompts)} clips Pexels descargados (0€)",
             0)
    return paths


def _fetch_pexels_clip(client: httpx.Client, api_key: str,
                        query: str, output_dir: Path, index: int) -> Path | None:
    """Busca y descarga el mejor clip HD de Pexels para la query."""
    r = client.get(
        PEXELS_VIDEO_API,
        headers={"Authorization": api_key},
        params={"query": query, "per_page": 5, "orientation": "landscape",
                "size": "medium"},
        timeout=30,
    )
    if r.status_code != 200:
        return None

    videos = r.json().get("videos", [])
    if not videos:
        return None

    # Elegir el primer vídeo con un archivo HD disponible (720p o superior)
    for video in videos:
        files = sorted(
            [f for f in video.get("video_files", [])
             if f.get("width", 0) >= 1280 and f.get("file_type") == "video/mp4"],
            key=lambda f: f.get("width", 0)
        )
        if not files:
            # Fallback: cualquier mp4
            files = [f for f in video.get("video_files", [])
                     if f.get("file_type") == "video/mp4"]

        if files:
            url = files[0]["link"]
            # Pexels URLs tienen tokens de tiempo limitado — descargar rápido
            resp = client.get(url, timeout=60, follow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 100_000:
                path = output_dir / f"hook_{index:02d}.mp4"
                path.write_bytes(resp.content)
                return path

    return None
