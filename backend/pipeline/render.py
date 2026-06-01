import subprocess
import json
from pathlib import Path
from ..database import add_step

def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])

def render_video(
    job_id: str,
    hook_videos: list[Path],
    body_images: list[Path],
    audio_path: Path,
    output_path: Path,
    title: str,
) -> Path:
    add_step(job_id, "render", "running", "Montando vídeo con FFmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent / f"tmp_{job_id}"
    tmp_dir.mkdir(exist_ok=True)

    audio_duration = get_duration(audio_path)
    hook_duration = len(hook_videos) * 5  # 5s por clip
    body_duration = audio_duration - hook_duration
    img_duration = body_duration / len(body_images) if body_images else 5

    # ── 1. Concatenar hook videos ──────────────────────────────────
    hook_list = tmp_dir / "hook_list.txt"
    hook_list.write_text("\n".join(f"file '{v.resolve()}'" for v in hook_videos))

    hook_concat = tmp_dir / "hook.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(hook_list),
        "-c:v", "libx264", "-preset", "fast", "-an",
        str(hook_concat)
    ], check=True, capture_output=True)

    # ── 2. Imágenes con Ken Burns (zoom/paneo aleatorio) ──────────
    img_clips = []
    for i, img in enumerate(body_images):
        out = tmp_dir / f"img_{i:02d}.mp4"
        zoompan = _ken_burns_filter(i, img_duration)
        subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", str(img),
            "-vf", zoompan,
            "-t", str(img_duration),
            "-c:v", "libx264", "-preset", "fast", "-an", "-r", "25",
            str(out)
        ], check=True, capture_output=True)
        img_clips.append(out)

    # ── 3. Concatenar imágenes ─────────────────────────────────────
    body_list = tmp_dir / "body_list.txt"
    body_list.write_text("\n".join(f"file '{v.resolve()}'" for v in img_clips))
    body_concat = tmp_dir / "body.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(body_list),
        "-c:v", "libx264", "-preset", "fast", "-an",
        str(body_concat)
    ], check=True, capture_output=True)

    # ── 4. Unir hook + body ────────────────────────────────────────
    full_list = tmp_dir / "full_list.txt"
    full_list.write_text(f"file '{hook_concat.resolve()}'\nfile '{body_concat.resolve()}'")
    full_video = tmp_dir / "full_video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(full_list),
        "-c:v", "libx264", "-preset", "fast", "-an",
        str(full_video)
    ], check=True, capture_output=True)

    # ── 5. Overlay texto final (suscríbete) + audio ────────────────
    cta_start = audio_duration - 8
    cta_filter = (
        f"drawtext=text='¡SUSCRÍBETE Y ACTIVA LA CAMPANITA!'"
        f":fontcolor=white:fontsize=48:box=1:boxcolor=red@0.8:boxborderw=10"
        f":x=(w-text_w)/2:y=h-100"
        f":enable='between(t,{cta_start},{audio_duration})'"
    )

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(full_video),
        "-i", str(audio_path),
        "-vf", cta_filter,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path)
    ], check=True, capture_output=True)

    add_step(job_id, "render", "done", f"Vídeo listo: {output_path.name}", 0)
    return output_path

def _ken_burns_filter(index: int, duration: float) -> str:
    effects = [
        # Zoom in lento desde centro
        f"scale=1920:1080,zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*25)}:s=1280x720",
        # Paneo izquierda → derecha
        f"scale=1440:810,zoompan=z=1.2:x='if(lte(x,0),0,x-1)':y='ih/2-(ih/zoom/2)':d={int(duration*25)}:s=1280x720",
        # Zoom out
        f"scale=1920:1080,zoompan=z='if(lte(zoom,1.0),1.3,max(zoom-0.001,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*25)}:s=1280x720",
        # Paneo derecha → izquierda + zoom leve
        f"scale=1440:810,zoompan=z=1.15:x='min(iw/2,x+0.5)':y='ih/2-(ih/zoom/2)':d={int(duration*25)}:s=1280x720",
    ]
    return effects[index % len(effects)]
