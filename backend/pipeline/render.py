import subprocess
import json
import re
from pathlib import Path
from ..database import add_step


def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _ffmpeg(*args):
    """Ejecuta FFmpeg y lanza error con stderr si falla."""
    result = subprocess.run(
        ["ffmpeg", "-y"] + list(args),
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Incluir las últimas líneas de stderr para diagnóstico claro
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"FFmpeg error (código {result.returncode}):\n{stderr_tail}")
    return result


def render_video(
    job_id: str,
    hook_videos: list[Path],
    body_images: list[Path],
    audio_path: Path,
    output_path: Path,
    title: str,
) -> Path:
    add_step(job_id, "render", "running", "Montando vídeo con FFmpeg")

    # ── Verificaciones previas ─────────────────────────────────────────────
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError(f"Audio no encontrado o vacío: {audio_path}")
    valid_images = [img for img in body_images if img.exists() and img.stat().st_size > 0]
    if len(valid_images) < 3:
        raise RuntimeError(f"Imágenes insuficientes para el vídeo: {len(valid_images)} (mínimo 3)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent / f"tmp_{job_id}"
    tmp_dir.mkdir(exist_ok=True)

    audio_duration = get_duration(audio_path)

    # ── 1. Hook: vídeos Kling SI existen, si no → primeras imágenes como intro ──
    valid_hook_videos = [v for v in hook_videos if v.exists() and v.stat().st_size > 0]
    hook_concat = tmp_dir / "hook.mp4"

    if valid_hook_videos:
        hook_duration = min(len(valid_hook_videos) * 5, audio_duration * 0.15)
        hook_list = tmp_dir / "hook_list.txt"
        hook_list.write_text("\n".join(f"file '{v.resolve()}'" for v in valid_hook_videos))
        _ffmpeg(
            "-f", "concat", "-safe", "0",
            "-i", str(hook_list),
            "-vf", "scale=1280:720",
            "-c:v", "libx264", "-preset", "fast", "-an",
            str(hook_concat)
        )
    else:
        # Fallback: usa las 3 primeras imágenes con Ken Burns como hook (15 seg)
        add_step(job_id, "render", "running", "Sin vídeos hook — usando imágenes intro como fallback")
        n_hook_imgs = min(3, len(valid_images))
        hook_duration = min(15, audio_duration * 0.15)
        img_dur_hook = hook_duration / n_hook_imgs
        hook_clips = []
        for i, img in enumerate(valid_images[:n_hook_imgs]):
            out = tmp_dir / f"hook_img_{i}.mp4"
            zoompan = _ken_burns_filter(i + 10, img_dur_hook)
            _ffmpeg(
                "-loop", "1", "-i", str(img),
                "-vf", zoompan, "-t", str(img_dur_hook),
                "-c:v", "libx264", "-preset", "fast", "-an", "-r", "25",
                str(out)
            )
            hook_clips.append(out)
        hook_list = tmp_dir / "hook_list.txt"
        hook_list.write_text("\n".join(f"file '{v.resolve()}'" for v in hook_clips))
        _ffmpeg(
            "-f", "concat", "-safe", "0",
            "-i", str(hook_list),
            "-c:v", "libx264", "-preset", "fast", "-an",
            str(hook_concat)
        )

    body_duration = audio_duration - hook_duration
    img_duration = body_duration / len(valid_images) if valid_images else 5

    # ── 2. Imágenes con Ken Burns ──────────────────────────────────────────
    add_step(job_id, "render", "running", f"Aplicando Ken Burns a {len(valid_images)} imágenes...")
    img_clips = []
    for i, img in enumerate(valid_images):
        out = tmp_dir / f"img_{i:02d}.mp4"
        zoompan = _ken_burns_filter(i, img_duration)
        _ffmpeg(
            "-loop", "1", "-i", str(img),
            "-vf", zoompan,
            "-t", str(img_duration),
            "-c:v", "libx264", "-preset", "fast", "-an", "-r", "25",
            str(out)
        )
        img_clips.append(out)

    # ── 3. Concatenar imágenes ─────────────────────────────────────────────
    body_list = tmp_dir / "body_list.txt"
    body_list.write_text("\n".join(f"file '{v.resolve()}'" for v in img_clips))
    body_concat = tmp_dir / "body.mp4"
    _ffmpeg(
        "-f", "concat", "-safe", "0",
        "-i", str(body_list),
        "-c:v", "libx264", "-preset", "fast", "-an",
        str(body_concat)
    )

    # ── 4. Unir hook + body ────────────────────────────────────────────────
    full_list = tmp_dir / "full_list.txt"
    full_list.write_text(f"file '{hook_concat.resolve()}'\nfile '{body_concat.resolve()}'")
    full_video = tmp_dir / "full_video.mp4"
    _ffmpeg(
        "-f", "concat", "-safe", "0",
        "-i", str(full_list),
        "-c:v", "libx264", "-preset", "fast", "-an",
        str(full_video)
    )

    # ── 5. Generar subtítulos estilo TikTok con Whisper ───────────────────
    add_step(job_id, "render", "running", "Transcribiendo audio para subtítulos (Whisper)...")
    ass_path = tmp_dir / "subtitles.ass"
    _generate_tiktok_subtitles(audio_path, ass_path)

    # ── 6. CTA overlay + audio ────────────────────────────────────────────
    add_step(job_id, "render", "running", "Añadiendo subtítulos, CTA y audio final...")
    cta_start = audio_duration - 8
    cta_filter = (
        f"subtitles={ass_path},"
        f"drawtext=text='¡SUSCRÍBETE Y ACTIVA LA CAMPANITA!'"
        f":fontcolor=white:fontsize=42:box=1:boxcolor=red@0.85:boxborderw=14"
        f":x=(w-text_w)/2:y=h*0.08"
        f":enable='between(t,{cta_start:.1f},{audio_duration:.1f})'"
    )

    _ffmpeg(
        "-i", str(full_video),
        "-i", str(audio_path),
        "-vf", cta_filter,
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path)
    )

    add_step(job_id, "render", "done", f"Vídeo listo con subtítulos: {output_path.name}", 0)
    return output_path


def _generate_tiktok_subtitles(audio_path: Path, ass_path: Path):
    """Transcribe audio con Whisper y genera subtítulos estilo TikTok (.ass)."""
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        str(audio_path),
        language="es",
        word_timestamps=True,
        vad_filter=True,
    )

    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append((w.start, w.end, w.word.strip()))

    groups = []
    chunk, chunk_start, chunk_end = [], None, None
    for start, end, word in words:
        if not word:
            continue
        if chunk_start is None:
            chunk_start = start
        chunk.append(word)
        chunk_end = end
        if len(chunk) >= 5:
            groups.append((chunk_start, chunk_end, " ".join(chunk)))
            chunk, chunk_start, chunk_end = [], None, None

    if chunk:
        groups.append((chunk_start, chunk_end, " ".join(chunk)))

    ass_path.write_text(_build_ass(groups), encoding="utf-8")


def _build_ass(groups: list) -> str:
    """Genera archivo ASS con fuente grande, negrita, centrada y sombra — estilo TikTok."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTok,Arial,58,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,3,2,2,40,40,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for start, end, text in groups:
        text = text.replace("{", "").replace("}", "").replace("\\n", " ").strip()
        if not text:
            continue
        s = _fmt_time(start)
        e = _fmt_time(end + 0.05)
        styled = f"{{\\an2}}{text.upper()}"
        lines.append(f"Dialogue: 0,{s},{e},TikTok,,0,0,0,,{styled}")

    return header + "\n".join(lines) + "\n"


def _fmt_time(seconds: float) -> str:
    """Convierte segundos a formato ASS: H:MM:SS.cs"""
    s = max(0, seconds)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    cs = int((sec - int(sec)) * 100)
    return f"{h}:{m:02d}:{int(sec):02d}.{cs:02d}"


def _ken_burns_filter(index: int, duration: float) -> str:
    d = int(duration * 25)
    effects = [
        f"scale=1920:1080,zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1440:810,zoompan=z=1.2:x='if(lte(x,0),0,x-1)':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1920:1080,zoompan=z='if(lte(zoom,1.0),1.3,max(zoom-0.001,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1440:810,zoompan=z=1.15:x='min(iw/2,x+0.5)':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
    ]
    return effects[index % len(effects)]
