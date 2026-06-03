import subprocess
import json
import math
import shutil
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from ..database import add_step

MUSIC_DIR = Path(__file__).parent.parent.parent / "music"

# Mapa tono → subcarpeta de música
MUSIC_TONE_MAP = {
    "misterio":     ["misterio", "neutro"],
    "drama":        ["drama", "misterio"],
    "motivacional": ["motivacional", "neutro"],
    "documental":   ["documental", "neutro"],
    "humor":        ["humor", "neutro"],
    "neutro":       ["neutro", "documental"],
}

def _pick_music(tone: str) -> Path | None:
    """Selecciona un MP3 aleatorio de la carpeta del tono. Fallback a carpetas alternativas."""
    folders = MUSIC_TONE_MAP.get(tone, ["neutro"])
    for folder in folders:
        d = MUSIC_DIR / folder
        if d.exists():
            mp3s = list(d.glob("*.mp3"))
            if mp3s:
                return random.choice(mp3s)
    # Buscar en cualquier subcarpeta
    all_mp3s = list(MUSIC_DIR.rglob("*.mp3"))
    return random.choice(all_mp3s) if all_mp3s else None

MAX_KB_DURATION = 8.0  # Ken Burns máx 8s por sub-clip — evita bug de zoompan con d>300
KB_WORKERS = 12        # ffmpeg Ken Burns en paralelo
FADE_SEC = 0.35        # fundido entre imágenes (segundos)


def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _ffmpeg(*args):
    result = subprocess.run(["ffmpeg", "-y"] + list(args), capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-10:])
        raise RuntimeError(f"FFmpeg error ({result.returncode}):\n{tail}")
    return result


def _color_grade(tone: str) -> str:
    """Filtro de color grading según el tono narrativo."""
    grades = {
        "misterio": "eq=contrast=1.12:brightness=-0.06:saturation=0.75,colorchannelmixer=rr=0.9:gg=0.95:bb=1.1",
        "drama":    "eq=contrast=1.15:brightness=-0.08:saturation=0.70,colorchannelmixer=rr=0.85:gg=0.90:bb=1.05",
        "motivacional": "eq=contrast=1.05:brightness=0.02:saturation=1.15",
        "documental":   "eq=contrast=1.08:brightness=-0.02:saturation=0.90",
        "humor":        "eq=contrast=1.0:brightness=0.03:saturation=1.10",
        "neutro":       "eq=contrast=1.05:brightness=-0.01:saturation=0.95",
    }
    return grades.get(tone, grades["neutro"])


def _image_to_clips(img: Path, total_dur: float, tmp_dir: Path,
                    base_idx: int) -> list[Path]:
    """Convierte 1 imagen a N sub-clips con Ken Burns + fade in/out en imagen completa."""
    n = max(1, math.ceil(total_dur / MAX_KB_DURATION))
    clip_dur = total_dur / n
    clips = []

    for j in range(n):
        out = tmp_dir / f"img_{base_idx:03d}_{j}.mp4"
        kb = _ken_burns(base_idx * 8 + j, clip_dur)

        # Fade in solo en el primer sub-clip, fade out solo en el último
        fade_parts = [kb]
        if j == 0 and FADE_SEC > 0:
            fade_parts.append(f"fade=t=in:st=0:d={FADE_SEC}")
        if j == n - 1 and FADE_SEC > 0:
            fade_parts.append(f"fade=t=out:st={max(0, clip_dur - FADE_SEC):.3f}:d={FADE_SEC}")

        vf = ",".join(fade_parts)

        _ffmpeg(
            "-loop", "1", "-i", str(img),
            "-vf", vf,
            "-t", f"{clip_dur:.4f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25",
            str(out)
        )
        clips.append(out)
    return clips


def _image_to_clips_batch(images: list[Path], img_dur: float,
                           tmp_dir: Path, base_idx: int = 0) -> list[list[Path]]:
    """Procesa todas las imágenes en paralelo con KB_WORKERS workers."""
    results: list[list[Path] | None] = [None] * len(images)

    def work(i: int, img: Path):
        return i, _image_to_clips(img, img_dur, tmp_dir, base_idx + i)

    with ThreadPoolExecutor(max_workers=KB_WORKERS) as ex:
        futs = {ex.submit(work, i, img): i for i, img in enumerate(images)}
        for f in as_completed(futs):
            i, clips = f.result()
            results[i] = clips

    return results  # type: ignore


def render_video(
    job_id: str,
    hook_clips: list[Path],
    body_images: list[Path],
    audio_path: Path,
    output_path: Path,
    title: str,
    hook_images: list[Path] = None,
    tone: str = "neutro",
) -> Path:
    add_step(job_id, "render", "running",
             f"Iniciando montaje — tono: {tone} · {KB_WORKERS} workers Ken Burns…")

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError(f"Audio vacío: {audio_path}")

    valid_body = [i for i in body_images if i.exists() and i.stat().st_size > 5000]
    if len(valid_body) < 3:
        raise RuntimeError(f"Solo {len(valid_body)} imágenes válidas (mínimo 3)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.parent / f"tmp_{job_id}"
    tmp.mkdir(exist_ok=True)

    audio_dur = get_duration(audio_path)
    add_step(job_id, "render", "running",
             f"Audio: {audio_dur/60:.1f} min | {len(valid_body)} imágenes | "
             f"color: {tone}")

    # ── HOOK ──────────────────────────────────────────────────────────────────
    hook_dur = min(25.0, audio_dur * 0.15)
    hook_concat = tmp / "hook.mp4"

    valid_hook_vids = [v for v in (hook_clips or []) if v.exists() and v.stat().st_size > 0]
    valid_hook_imgs = [i for i in (hook_images or []) if i.exists() and i.stat().st_size > 5000]

    if valid_hook_vids:
        lst = tmp / "hv_list.txt"
        lst.write_text("\n".join(f"file '{v.resolve()}'" for v in valid_hook_vids))
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst),
                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                "-c:v", "libx264", "-preset", "ultrafast", "-an", str(hook_concat))
    elif valid_hook_imgs:
        per_img = hook_dur / len(valid_hook_imgs)
        hook_clip_groups = _image_to_clips_batch(valid_hook_imgs, per_img, tmp, base_idx=900)
        clips_h = [c for group in hook_clip_groups for c in group]
        _write_concat(tmp / "hi_list.txt", clips_h)
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "hi_list.txt"),
                "-c:v", "libx264", "-preset", "ultrafast", "-an", str(hook_concat))
    else:
        n_h = min(4, len(valid_body))
        per_h = hook_dur / n_h
        hook_clip_groups = _image_to_clips_batch(valid_body[:n_h], per_h, tmp, base_idx=900)
        clips_h = [c for group in hook_clip_groups for c in group]
        _write_concat(tmp / "hf_list.txt", clips_h)
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "hf_list.txt"),
                "-c:v", "libx264", "-preset", "ultrafast", "-an", str(hook_concat))

    # ── CUERPO: Ken Burns + Whisper EN PARALELO ───────────────────────────────
    body_dur = audio_dur - hook_dur
    img_dur = body_dur / len(valid_body)
    n_subclips = math.ceil(img_dur / MAX_KB_DURATION)
    add_step(job_id, "render", "running",
             f"Ken Burns paralelo ({KB_WORKERS} workers) + Whisper simultáneo — "
             f"{len(valid_body)} imgs × {n_subclips} sub-clips…")

    ass_path = tmp / "subs.ass"

    # Lanzar Whisper en background MIENTRAS se procesan los Ken Burns
    with ThreadPoolExecutor(max_workers=1) as whisper_ex:
        whisper_fut = whisper_ex.submit(_whisper_subtitles, audio_path, ass_path)

        clip_groups = _image_to_clips_batch(valid_body, img_dur, tmp, base_idx=0)
        all_body_clips = [c for group in clip_groups for c in group]

        _write_concat(tmp / "body_list.txt", all_body_clips)
        body_mp4 = tmp / "body.mp4"
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "body_list.txt"),
                "-c:v", "libx264", "-preset", "ultrafast", "-an", str(body_mp4))

        _write_concat(tmp / "full_list.txt", [hook_concat, body_mp4])
        full_mp4 = tmp / "full.mp4"
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "full_list.txt"),
                "-c:v", "libx264", "-preset", "ultrafast", "-an", str(full_mp4))

        subs_ok = whisper_fut.result()

    music_path = _pick_music(tone)
    has_music = music_path is not None and music_path.exists()
    add_step(job_id, "render", "running",
             f"Subtítulos {'✓' if subs_ok else '✗'} · música {'✓ ' + music_path.stem[:30] if has_music else '✗ (sin música aún)'} · encode final…")

    # ── MEZCLA FINAL: audio + música + subtítulos + color grade + CTA ─────────
    cta_start = max(0, audio_dur - 12)
    grade = _color_grade(tone)

    if subs_ok:
        esc = str(ass_path.resolve()).replace("'", "\\'").replace(":", "\\:")
        vf = (
            f"subtitles='{esc}',"
            f"{grade},"
            f"drawtext=text='¡SUSCRÍBETE Y ACTIVA LA CAMPANITA!':"
            f"fontcolor=white:fontsize=36:box=1:boxcolor=red@0.88:boxborderw=14:"
            f"x=(w-text_w)/2:y=h*0.06:enable='between(t,{cta_start:.1f},{audio_dur:.1f})'"
        )
    else:
        vf = (
            f"{grade},"
            f"drawtext=text='¡SUSCRÍBETE Y ACTIVA LA CAMPANITA!':"
            f"fontcolor=white:fontsize=36:box=1:boxcolor=red@0.88:boxborderw=14:"
            f"x=(w-text_w)/2:y=h*0.06:enable='between(t,{cta_start:.1f},{audio_dur:.1f})'"
        )

    if has_music:
        # Mezcla: narración al 100% + música a -22dB de fondo (barely noticeable, cinematic)
        _ffmpeg(
            "-i", str(full_mp4),
            "-i", str(audio_path),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex",
            f"[1:a]volume=1.0[voice];[2:a]volume=0.08,atrim=0:duration={audio_dur:.2f},afade=t=out:st={max(0,audio_dur-3):.1f}:d=3[music];[voice][music]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path)
        )
    else:
        _ffmpeg(
            "-i", str(full_mp4),
            "-i", str(audio_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path)
        )

    try:
        shutil.rmtree(tmp)
    except Exception:
        pass

    size_mb = output_path.stat().st_size / 1024 / 1024

    # ── AUTO-GENERAR SHORT VERTICAL (gratis, segunda fuente de ingresos) ──────
    short_path = output_path.parent / "short.mp4"
    try:
        add_step(job_id, "render", "running", "Generando Short vertical 9:16 automático…")
        _generate_short(output_path, short_path, audio_dur)
        short_mb = short_path.stat().st_size / 1024 / 1024
        add_step(job_id, "render", "done",
                 f"✅ Vídeo {size_mb:.0f}MB · Short {short_mb:.0f}MB · "
                 f"{audio_dur/60:.0f} min · subtítulos {'✓' if subs_ok else '✗'}", 0)
    except Exception as e:
        add_step(job_id, "render", "done",
                 f"Vídeo listo: {size_mb:.0f} MB · {audio_dur/60:.0f} min "
                 f"(Short falló: {str(e)[:40]})", 0)

    return output_path


def _generate_short(video_path: Path, output_path: Path, total_dur: float):
    """
    Genera un Short vertical 9:16 de 58s con los momentos más impactantes:
    - Primeros 20s (hook) + segundos 300-330 (mid reveal) + últimos 8s (CTA)
    Escala al centro para llenar el frame vertical.
    """
    short_dur = min(58.0, total_dur)
    # Tomar el hook completo si el vídeo es largo, si no el vídeo entero
    if total_dur > 120:
        # Segmento 1: primeros 30s (hook), Segmento 2: mitad -15s, Segmento 3: últimos 8s
        seg1_end = 30.0
        mid = total_dur / 2
        seg2_start = max(seg1_end + 5, mid - 10)
        seg2_end = seg2_start + 12
        seg3_start = max(seg2_end + 5, total_dur - 10)
        seg3_end = min(total_dur, seg3_start + 8)
        # Calcular duración total real
        actual_dur = (seg1_end + (seg2_end - seg2_start) + (seg3_end - seg3_start))
        actual_dur = min(actual_dur, short_dur)
    else:
        seg1_end = short_dur
        seg2_start = seg2_end = seg3_start = seg3_end = 0

    # Filtro: escala 16:9 → recorte centro → 720×1280 (9:16)
    # scale=-1:1280 → 2275×1280, luego crop=720:1280 centro
    vf_short = (
        "scale=-1:1280,"
        "crop=720:1280,"
        "drawtext=text='¡SUSCRÍBETE!':"
        "fontcolor=white:fontsize=52:box=1:boxcolor=black@0.7:boxborderw=10:"
        "x=(w-text_w)/2:y=h*0.88:"
        f"enable='between(t,{max(0,actual_dur-6):.0f},{actual_dur:.0f})'"
        if total_dur > 120 else
        "scale=-1:1280,crop=720:1280"
    )

    if total_dur > 120:
        # Crear segmentos y concatenar
        import tempfile
        tmp = output_path.parent / "_short_tmp"
        tmp.mkdir(exist_ok=True)

        segs = [
            (0, seg1_end),
            (seg2_start, seg2_end),
            (seg3_start, seg3_end),
        ]
        seg_files = []
        for idx, (ss, se) in enumerate(segs):
            if se <= ss:
                continue
            seg_out = tmp / f"seg_{idx}.mp4"
            _ffmpeg(
                "-ss", f"{ss:.2f}", "-to", f"{se:.2f}",
                "-i", str(video_path),
                "-vf", "scale=-1:1280,crop=720:1280",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                str(seg_out)
            )
            seg_files.append(seg_out)

        list_file = tmp / "short_list.txt"
        list_file.write_text("\n".join(f"file '{f.resolve()}'" for f in seg_files))
        _ffmpeg(
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-vf", "drawtext=text='¡SUSCRÍBETE!':"
                   "fontcolor=white:fontsize=52:box=1:boxcolor=black@0.7:boxborderw=10:"
                   "x=(w-text_w)/2:y=h*0.88",
            "-c:v", "libx264", "-preset", "fast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        )
        try:
            shutil.rmtree(tmp)
        except Exception:
            pass
    else:
        _ffmpeg(
            "-i", str(video_path),
            "-t", f"{short_dur:.2f}",
            "-vf", "scale=-1:1280,crop=720:1280",
            "-c:v", "libx264", "-preset", "fast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_concat(path: Path, clips: list[Path]):
    path.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))


def _whisper_subtitles(audio_path: Path, ass_path: Path) -> bool:
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), language="es",
                                       word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    if w.word.strip():
                        words.append((w.start, w.end, w.word.strip()))

        # 3 palabras por grupo = más dinámico (TikTok style)
        groups, chunk, cs, ce = [], [], None, None
        for s, e, w in words:
            if cs is None:
                cs = s
            chunk.append(w)
            ce = e
            if len(chunk) >= 3:
                groups.append((cs, ce, " ".join(chunk)))
                chunk, cs, ce = [], None, None
        if chunk:
            groups.append((cs, ce, " ".join(chunk)))

        ass_path.write_text(_build_ass(groups), encoding="utf-8")
        return True
    except Exception:
        return False


def _build_ass(groups: list) -> str:
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Texto blanco, sombra negra gruesa, fuente grande — estilo True Crime
        "Style: TikTok,Arial,68,&H00FFFFFF,&H000000FF,&H00000000,&HCC000000,"
        "-1,0,0,0,100,100,1,0,1,4,2,2,40,40,100,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for s, e, text in groups:
        text = text.replace("{", "").replace("}", "").replace("\\n", " ").strip()
        if not text:
            continue
        lines.append(
            f"Dialogue: 0,{_t(s)},{_t(e+0.05)},TikTok,,0,0,0,,"
            f"{{\\an2}}{text.upper()}"
        )
    return header + "\n".join(lines) + "\n"


def _t(seconds: float) -> str:
    s = max(0.0, seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{int(sec):02d}.{int((sec%1)*100):02d}"


def _ken_burns(idx: int, dur: float) -> str:
    """12 efectos distintos. dur limitado a MAX_KB_DURATION por _image_to_clips."""
    d = max(1, int(dur * 25))
    fx = [
        f"scale=1920:1080,zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1440:810,zoompan=z=1.2:x='iw/2-(iw/zoom/2)+((iw/zoom/4)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1920:1080,zoompan=z='max(1.3-0.001*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1440:810,zoompan=z=1.2:x='iw/2-(iw/zoom/2)-((iw/zoom/4)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1920:1080,zoompan=z='min(zoom+0.0008,1.25)':x='iw/2-(iw/zoom/2)':y='ih-(ih/zoom)':d={d}:s=1280x720",
        f"scale=1440:810,zoompan=z=1.2:x='iw/2-(iw/zoom/2)':y='ih/zoom*(0.9-0.4*on/{d})':d={d}:s=1280x720",
        f"scale=1920:1080,zoompan=z='min(zoom+0.001,1.3)':x='iw*0.1':y='ih*0.1':d={d}:s=1280x720",
        f"scale=1440:810,zoompan=z=1.2:x='(iw/zoom/2)*(0.6+0.8*on/{d})':y='(ih/zoom/2)*(0.6+0.8*on/{d})':d={d}:s=1280x720",
        f"scale=1920:1080,zoompan=z='1.1+0.1*sin(3.14*on/{d})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
        f"scale=1440:810,zoompan=z=1.2:x='iw/2-(iw/zoom/2)':y='ih/zoom*(0.5+0.4*on/{d})':d={d}:s=1280x720",
        f"scale=1920:1080,zoompan=z='min(zoom+0.001,1.3)':x='iw*0.7':y='ih*0.7':d={d}:s=1280x720",
        f"scale=1920:1080,zoompan=z='max(1.2-0.0005*on,1.05)':x='iw/2-(iw/zoom/2)+((iw/zoom/6)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1280x720",
    ]
    return fx[idx % len(fx)]
