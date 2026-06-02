import os
import httpx
import asyncio
import anthropic
from pathlib import Path
from ..database import add_cost_event, add_step

FAL_SYNC_BASE = "https://fal.run"          # síncrono — espera resultado
FAL_QUEUE_BASE = "https://queue.fal.run"   # asíncrono — polling
FLUX_SCHNELL = "fal-ai/flux/schnell"

def generate_image_prompts(job_id: str, script: str, niche: str, count: int = 15) -> list[str]:
    """Usa Claude Haiku (barato) para extraer prompts de imagen del guión."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""Del siguiente guión sobre '{niche}', extrae {count} momentos visuales clave.
Para cada uno, escribe un prompt en inglés para generar una imagen con IA.
Los prompts deben ser: cinematográficos, detallados, photorealistic, 16:9.
Devuelve SOLO los prompts, uno por línea, sin numeración ni explicaciones.

GUIÓN (primeras 3000 palabras):
{script[:3000]}"""
        }]
    )

    prompts = [p.strip() for p in msg.content[0].text.strip().split("\n") if p.strip()][:count]

    # Coste Haiku: casi gratis
    cost = (msg.usage.input_tokens * 0.8 + msg.usage.output_tokens * 4) / 1_000_000 * 0.92
    add_cost_event(job_id, "claude_haiku", msg.usage.output_tokens, 4 / 1_000_000, cost)

    return prompts

def _fal_generate_image(client: httpx.Client, prompt: str, headers: dict) -> str:
    """Llama a FAL.ai en modo síncrono. Devuelve URL de la imagen."""
    resp = client.post(
        f"{FAL_SYNC_BASE}/{FLUX_SCHNELL}",
        headers=headers,
        json={"prompt": prompt, "image_size": "landscape_16_9", "num_images": 1},
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"FAL error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    images = data.get("images") or data.get("output", {}).get("images", [])
    if not images:
        raise RuntimeError(f"FAL no devolvió imágenes: {str(data)[:200]}")
    return images[0]["url"]


def generate_images(job_id: str, prompts: list[str], output_dir: Path) -> list[Path]:
    add_step(job_id, "images", "running", f"Generando {len(prompts)} imágenes con Flux Schnell")
    output_dir.mkdir(parents=True, exist_ok=True)
    fal_key = os.environ["FAL_API_KEY"]
    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
    paths = []

    with httpx.Client(timeout=180) as client:
        for i, prompt in enumerate(prompts):
            try:
                img_url = _fal_generate_image(client, prompt, headers)
                img_resp = client.get(img_url, timeout=60)
                img_resp.raise_for_status()
                path = output_dir / f"img_{i:02d}.jpg"
                path.write_bytes(img_resp.content)
                paths.append(path)
                add_cost_event(job_id, "flux_schnell", 1, 0.003, 0.003 * 0.92)
            except Exception as e:
                # Fallback: Pollinations (gratis)
                path = _pollinations_fallback(client, prompt, output_dir, i)
                if path:
                    paths.append(path)

    add_step(job_id, "images", "done", f"{len(paths)} imágenes generadas", len(paths) * 0.003 * 0.92)
    return paths


def generate_thumbnail(job_id: str, title: str, niche: str, output_dir: Path) -> Path:
    add_step(job_id, "thumbnail", "running", "Generando miniatura")
    fal_key = os.environ["FAL_API_KEY"]
    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}

    prompt = (
        f"YouTube thumbnail, dramatic cinematic, topic: {title}, niche: {niche}, "
        "bold colors, high contrast, photorealistic, 16:9, no text overlays, "
        "professional photography, eye-catching, viral style"
    )

    with httpx.Client(timeout=180) as client:
        try:
            img_url = _fal_generate_image(client, prompt, headers)
            img_resp = client.get(img_url, timeout=60)
            img_resp.raise_for_status()
            path = output_dir / "thumbnail.jpg"
            path.write_bytes(img_resp.content)
        except Exception as e:
            # Fallback Pollinations
            path = _pollinations_fallback(client, prompt, output_dir, 99)
            if not path:
                raise RuntimeError(f"Thumbnail falló en FAL y Pollinations: {e}")

    add_cost_event(job_id, "flux_schnell_thumb", 1, 0.003, 0.003 * 0.92)
    add_step(job_id, "thumbnail", "done", "Miniatura generada", 0.003 * 0.92)
    return path

def _pollinations_fallback(client, prompt, output_dir, index) -> Path | None:
    try:
        encoded = prompt[:200].replace(" ", "%20")
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1280&height=720&nologo=true"
        resp = client.get(url, timeout=30)
        if resp.status_code == 200:
            path = output_dir / f"img_{index:02d}.jpg"
            path.write_bytes(resp.content)
            return path
    except Exception:
        pass
    return None
