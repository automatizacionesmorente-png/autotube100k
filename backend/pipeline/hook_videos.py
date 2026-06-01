import os
import jwt
import time
import httpx
import anthropic
from pathlib import Path
from ..database import add_cost_event, add_step

KLING_BASE = "https://api.klingai.com"

def _get_kling_token() -> str:
    payload = {
        "iss": os.environ["KLING_ACCESS_KEY"],
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5,
    }
    return jwt.encode(payload, os.environ["KLING_SECRET_KEY"], algorithm="HS256")

def generate_hook_prompts(job_id: str, script: str, niche: str, count: int = 5) -> list[str]:
    """Claude Haiku genera prompts cinematográficos para el hook."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": f"""Eres experto en hooks virales de YouTube.
Del siguiente guión sobre '{niche}', crea {count} prompts para vídeos AI de 5 segundos que formen el gancho inicial.
Los vídeos deben ser IMPACTANTES, cinematográficos, que enganchen al espectador en los primeros segundos.
Prompts en inglés, muy descriptivos, con movimiento de cámara, iluminación dramática.
SOLO los prompts, uno por línea.

INICIO DEL GUIÓN:
{script[:1500]}"""
        }]
    )

    prompts = [p.strip() for p in msg.content[0].text.strip().split("\n") if p.strip()][:count]
    cost = (msg.usage.input_tokens * 0.8 + msg.usage.output_tokens * 4) / 1_000_000 * 0.92
    add_cost_event(job_id, "claude_haiku", msg.usage.output_tokens, 4 / 1_000_000, cost)
    return prompts

def generate_hook_videos(job_id: str, prompts: list[str], output_dir: Path) -> list[Path]:
    add_step(job_id, "hook_videos", "running", f"Generando {len(prompts)} vídeos hook con Kling")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    with httpx.Client(timeout=300) as client:
        for i, prompt in enumerate(prompts):
            try:
                token = _get_kling_token()
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }

                # Crear tarea de vídeo
                resp = client.post(
                    f"{KLING_BASE}/v1/videos/text2video",
                    headers=headers,
                    json={
                        "model": "kling-v1",
                        "prompt": prompt,
                        "negative_prompt": "blurry, low quality, static, text, watermark",
                        "cfg_scale": 0.5,
                        "mode": "std",  # standard = más barato que pro
                        "duration": "5",
                        "aspect_ratio": "16:9",
                    }
                )
                resp.raise_for_status()
                task_id = resp.json()["data"]["task_id"]

                # Poll hasta completar
                video_url = _poll_kling(client, task_id)
                video_resp = client.get(video_url, timeout=120)
                path = output_dir / f"hook_{i:02d}.mp4"
                path.write_bytes(video_resp.content)
                paths.append(path)

                # Coste Kling Standard 5s: ~$0.14
                add_cost_event(job_id, "kling_standard", 1, 0.14, 0.14 * 0.92)

            except Exception as e:
                add_step(job_id, "hook_videos", "warning", f"Clip {i} falló: {e}")

    add_step(job_id, "hook_videos", "done", f"{len(paths)} vídeos hook generados",
             len(paths) * 0.14 * 0.92)
    return paths

def _poll_kling(client: httpx.Client, task_id: str, max_tries: int = 60) -> str:
    for _ in range(max_tries):
        token = _get_kling_token()
        resp = client.get(
            f"{KLING_BASE}/v1/videos/text2video/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()["data"]
        status = data.get("task_status")

        if status == "succeed":
            return data["task_result"]["videos"][0]["url"]
        if status == "failed":
            raise RuntimeError(f"Kling task failed: {data.get('task_status_msg')}")

        time.sleep(10)
    raise TimeoutError("Kling task timed out after 10 minutes")
