"""
Hook videos — desactivado temporalmente.

El render usa automáticamente las primeras 3 imágenes del vídeo
con efecto Ken Burns como intro hook (0€ extra).

Cuando el canal tenga ingresos, activar MiniMax Video en FAL.ai
(fal-ai/minimax/video-01, $0.50/clip × 5 = $2.50/vídeo).
"""
from pathlib import Path
from ..database import add_step


def generate_hook_prompts(job_id: str, script: str, niche: str, count: int = 5) -> list[str]:
    return []


def generate_hook_videos(job_id: str, prompts: list[str], output_dir: Path) -> list[Path]:
    add_step(job_id, "hook_videos", "done",
             "Hook con imágenes cinematográficas (0€)", 0)
    return []
