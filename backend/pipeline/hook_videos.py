import os
import time
import httpx
import anthropic
from pathlib import Path
from ..database import add_cost_event, add_step

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_SYNC_BASE  = "https://fal.run"

# Modelo de vídeo en FAL.ai — MiniMax Video 01 (texto → vídeo 6s, 720p)
VIDEO_MODEL = "fal-ai/minimax/video-01"
# Coste real: ~$0.07 por clip de 6s en FAL.ai
VIDEO_COST_USD = 0.07
EUR_PER_USD = 0.92


def generate_hook_prompts(job_id: str, script: str, niche: str, count: int = 5) -> list[str]:
    """Claude Haiku genera prompts cinematográficos para el hook."""
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": f"""Eres experto en hooks virales de YouTube.
Del siguiente guión sobre '{niche}', crea {count} prompts para vídeos AI de 6 segundos que formen el gancho inicial.
Los vídeos deben ser IMPACTANTES, cinematográficos, que enganchen al espectador en los primeros segundos.
Prompts en inglés, muy descriptivos, con movimiento de cámara, iluminación dramática.
SOLO los prompts, uno por línea, sin numeración.

INICIO DEL GUIÓN:
{script[:1500]}"""}]
        )
        prompts = [p.strip() for p in msg.content[0].text.strip().split("\n") if p.strip()][:count]
        cost = (msg.usage.input_tokens * 0.8 + msg.usage.output_tokens * 4) / 1_000_000 * EUR_PER_USD
        add_cost_event(job_id, "claude_haiku", msg.usage.output_tokens, 4 / 1_000_000, cost)
        return prompts
    except Exception:
        # Fallback genérico si Claude falla
        return [
            f"dramatic cinematic close-up, mysterious atmosphere, {niche}, dark lighting, camera push-in, 4K",
            f"aerial drone shot over ancient ruins, golden hour, epic scale, {niche}, cinematic",
            f"slow motion reveal, dark corridor with light at end, symbolic of {niche}, dramatic",
            f"extreme close-up of human eye reflecting fire, {niche} concept, high contrast",
            f"time-lapse storm clouds over city, {niche} metaphor, dramatic colors, cinematic",
        ][:5]


def generate_hook_videos(job_id: str, prompts: list[str], output_dir: Path) -> list[Path]:
    """Genera vídeos hook usando MiniMax Video en FAL.ai."""
    add_step(job_id, "hook_videos", "running", f"Generando {len(prompts)} vídeos hook con MiniMax AI...")
    output_dir.mkdir(parents=True, exist_ok=True)

    fal_key = os.environ["FAL_API_KEY"]
    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
    paths = []

    with httpx.Client(timeout=300) as client:
        for i, prompt in enumerate(prompts):
            add_step(job_id, "hook_videos", "running", f"Clip {i+1}/{len(prompts)}: generando vídeo...")
            try:
                path = _generate_fal_video(client, prompt, headers, output_dir, i)
                if path:
                    paths.append(path)
                    add_cost_event(job_id, "minimax_video", 1, VIDEO_COST_USD, VIDEO_COST_USD * EUR_PER_USD)
            except Exception as e:
                add_step(job_id, "hook_videos", "running",
                         f"Clip {i+1} falló ({str(e)[:60]}), continuando...")

    count = len(paths)
    add_step(job_id, "hook_videos", "done",
             f"{count}/{len(prompts)} vídeos hook generados" if count > 0 else "Sin vídeos hook — usará imágenes intro",
             count * VIDEO_COST_USD * EUR_PER_USD)
    return paths


def _generate_fal_video(client: httpx.Client, prompt: str, headers: dict,
                         output_dir: Path, index: int) -> Path | None:
    """Genera un vídeo con MiniMax Video en FAL.ai usando cola asíncrona."""

    # 1. Enviar a la cola
    resp = client.post(
        f"{FAL_QUEUE_BASE}/{VIDEO_MODEL}",
        headers=headers,
        json={
            "prompt": prompt,
            "prompt_optimizer": True,
        },
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"FAL queue error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    request_id = data.get("request_id")
    if not request_id:
        raise RuntimeError(f"No request_id en respuesta FAL: {str(data)[:200]}")

    # 2. Polling hasta completar (máx 4 minutos)
    status_url = f"{FAL_QUEUE_BASE}/{VIDEO_MODEL}/requests/{request_id}/status"
    result_url = f"{FAL_QUEUE_BASE}/{VIDEO_MODEL}/requests/{request_id}"

    for attempt in range(48):  # 48 × 5s = 4 minutos
        time.sleep(5)
        status_resp = client.get(status_url, headers=headers, timeout=30)
        status_data = status_resp.json()
        status = status_data.get("status", "")

        if status == "COMPLETED":
            break
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"FAL vídeo {status}: {str(status_data)[:200]}")
    else:
        raise TimeoutError(f"FAL vídeo timeout tras 4 minutos (request_id={request_id})")

    # 3. Obtener resultado
    result_resp = client.get(result_url, headers=headers, timeout=30)
    result_data = result_resp.json()

    video_url = (
        result_data.get("video", {}).get("url")
        or (result_data.get("videos") or [{}])[0].get("url")
    )
    if not video_url:
        raise RuntimeError(f"No video URL en resultado FAL: {str(result_data)[:200]}")

    # 4. Descargar vídeo
    video_resp = client.get(video_url, timeout=120)
    video_resp.raise_for_status()
    path = output_dir / f"hook_{index:02d}.mp4"
    path.write_bytes(video_resp.content)
    return path
