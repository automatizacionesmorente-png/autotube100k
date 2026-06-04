import subprocess
import json
import math
import shutil
import random
import threading
import time as _time
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

# ── Resolución de salida ────────────────────────────────────────────────────
OUT_W, OUT_H = 1920, 1080   # 1080p Full HD — calidad profesional YouTube
OUT_RES = f"{OUT_W}x{OUT_H}"

MAX_KB_DURATION = 6.0  # Ken Burns máx 6s por sub-clip — cambio visual más frecuente
KB_WORKERS = 12        # ffmpeg Ken Burns en paralelo
FADE_SEC = 0.25        # fundido entre imágenes (segundos) — más rápido = más dinámico


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


def _video_to_shot(video: Path, dur: float, out: Path, idx: int = 0):
    """
    Convierte un clip de Pexels en un plano de `dur`s a 1920x1080, 25fps.
    Añade zoom-in sutil (1.0→1.08) para dinamismo — igual que Ken Burns en imágenes.
    """
    try:
        vdur = get_duration(video)
    except Exception:
        vdur = 0

    # Zoom sutil que varía según el índice (alternamos zoom-in / zoom-out)
    zoom_effects = [
        "zoompan=z='min(zoom+0.0003,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        "zoompan=z='max(1.08-0.0003*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        "zoompan=z='min(zoom+0.0002,1.06)':x='iw/2-(iw/zoom/2)+1':y='ih/2-(ih/zoom/2)'",
        "zoompan=z=1.05:x='iw/2-(iw/zoom/2)+on*0.3':y='ih/2-(ih/zoom/2)'",
    ]
    zoom = zoom_effects[idx % len(zoom_effects)]
    d_frames = max(1, int(dur * 25))

    vf = (
        f"scale=2880:1620:force_original_aspect_ratio=increase,crop=2880:1620,fps=25,"
        f"{zoom}:d={d_frames}:s=1920x1080,"
        f"fade=t=in:st=0:d={FADE_SEC},fade=t=out:st={max(0,dur-FADE_SEC):.2f}:d={FADE_SEC}"
    )

    if vdur >= dur:
        _ffmpeg("-ss", "0", "-t", f"{dur:.2f}", "-i", str(video),
                "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25", str(out))
    else:
        _ffmpeg("-stream_loop", "-1", "-t", f"{dur:.2f}", "-i", str(video),
                "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25", str(out))
    return out


def _build_dynamic_body(images: list[Path], videos: list[Path],
                        body_dur: float, tmp: Path) -> list[Path]:
    """
    Construye el cuerpo intercalando imágenes (Ken Burns) con clips de vídeo real.
    Plano nuevo cada ~body_dur/N segundos. Devuelve la lista ordenada de clips mp4.
    """
    # Intercalar: ~1 vídeo cada `gap` imágenes, repartido por todo el cuerpo
    shots = []  # ('img'|'vid', path)
    gap = max(1, round(len(images) / len(videos))) if videos else 10**9
    vi = 0
    for i, img in enumerate(images):
        shots.append(("img", img))
        if videos and (i + 1) % gap == 0 and vi < len(videos):
            shots.append(("vid", videos[vi])); vi += 1
    while videos and vi < len(videos):
        shots.append(("vid", videos[vi])); vi += 1

    n = len(shots)
    per_shot = body_dur / n
    results: list[list[Path] | None] = [None] * n

    def work(idx, kind, path):
        if kind == "img":
            return idx, _image_to_clips(path, per_shot, tmp, idx)
        else:
            out = tmp / f"vid_{idx:03d}.mp4"
            try:
                _video_to_shot(path, per_shot, out, idx)
                return idx, [out]
            except Exception:
                # si el vídeo falla, rellenar con una imagen como respaldo
                return idx, _image_to_clips(images[idx % len(images)], per_shot, tmp, 5000 + idx)

    with ThreadPoolExecutor(max_workers=KB_WORKERS) as ex:
        futs = [ex.submit(work, i, k, p) for i, (k, p) in enumerate(shots)]
        for f in as_completed(futs):
            idx, clips = f.result()
            results[idx] = clips

    ordered = []
    for group in results:
        if group:
            ordered.extend(group)
    return ordered


def render_video(
    job_id: str,
    hook_clips: list[Path],
    body_images: list[Path],
    audio_path: Path,
    output_path: Path,
    title: str,
    hook_images: list[Path] = None,
    tone: str = "neutro",
    body_videos: list[Path] = None,
    progress_cb=None,   # callable(pct, eta_secs, done, total, phase) — datos 100% reales
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
        # Recortar cada clip de Pexels a ~clip_len s para que el hook total ≈ hook_dur
        clip_len = max(3.5, min(6.0, hook_dur / max(1, len(valid_hook_vids))))
        trimmed = []
        for k, v in enumerate(valid_hook_vids):
            t_out = tmp / f"hvtrim_{k:02d}.mp4"
            _ffmpeg("-i", str(v), "-t", f"{clip_len:.2f}",
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,"
                           "crop=1920:1080,fade=t=in:st=0:d=0.3,"
                           f"fade=t=out:st={max(0,clip_len-0.3):.2f}:d=0.3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-an", "-r", "25",
                    str(t_out))
            trimmed.append(t_out)
        lst = tmp / "hv_list.txt"
        lst.write_text("\n".join(f"file '{v.resolve()}'" for v in trimmed))
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst),
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

    # ── CUERPO DINÁMICO: imágenes (Ken Burns) + vídeo real Pexels + Whisper ────
    body_dur = audio_dur - hook_dur
    valid_vids = [v for v in (body_videos or []) if v.exists() and v.stat().st_size > 50000]
    total_shots = len(valid_body) + len(valid_vids)
    add_step(job_id, "render", "running",
             f"Montaje dinámico: {len(valid_body)} imágenes + {len(valid_vids)} clips de vídeo real "
             f"= {total_shots} planos (~{body_dur/max(1,total_shots):.0f}s c/u) · Whisper simultáneo…")

    # ── PROGRESO REAL: cuántos clips Ken Burns se esperan (datos 100% reales) ──
    # Solo img_*.mp4 + vid_*.mp4 = la parte lenta (Ken Burns paralelo)
    # hvtrim_*.mp4 (hook Pexels) son rápidos y no se cuentan
    _per_shot      = body_dur / max(1, total_shots)
    _n_sub_img     = max(1, math.ceil(_per_shot / MAX_KB_DURATION))
    _exp_body      = len(valid_body) * _n_sub_img + len(valid_vids)
    # Hook: solo si usa imágenes (hook con vídeos Pexels no crea img_*.mp4)
    _exp_hook_img  = 0
    if not valid_hook_vids:
        _vh_imgs = valid_hook_imgs if valid_hook_imgs else valid_body[:min(4, len(valid_body))]
        _ph      = hook_dur / max(1, len(_vh_imgs))
        _exp_hook_img = len(_vh_imgs) * max(1, math.ceil(_ph / MAX_KB_DURATION))
    _total_expected = max(1, _exp_body + _exp_hook_img)
    _render_start   = _time.time()
    _stop_monitor   = threading.Event()

    def _count_clips() -> int:
        try:
            return len(list(tmp.glob("img_*.mp4"))) + len(list(tmp.glob("vid_*.mp4")))
        except Exception:
            return 0

    def _monitor_loop():
        _last_done = 0
        while not _stop_monitor.wait(20):
            try:
                done = _count_clips()
                if done <= _last_done:
                    continue
                _last_done = done
                pct     = min(85.0, done / _total_expected * 100.0)
                elapsed = _time.time() - _render_start
                frac    = pct / 100.0
                eta     = (elapsed / frac) * (1.0 - frac) if frac > 0.005 else 0.0
                if progress_cb:
                    progress_cb(pct, eta, done, _total_expected, "clips")
            except Exception:
                pass

    if progress_cb:
        threading.Thread(target=_monitor_loop, daemon=True).start()

    ass_path = tmp / "subs.ass"

    # Lanzar Whisper en background MIENTRAS se procesan los planos
    with ThreadPoolExecutor(max_workers=1) as whisper_ex:
        whisper_fut = whisper_ex.submit(_whisper_subtitles, audio_path, ass_path)

        all_body_clips = _build_dynamic_body(valid_body, valid_vids, body_dur, tmp)

        _write_concat(tmp / "body_list.txt", all_body_clips)
        body_mp4 = tmp / "body.mp4"
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "body_list.txt"),
                "-c:v", "libx264", "-preset", "ultrafast", "-an", str(body_mp4))

        _write_concat(tmp / "full_list.txt", [hook_concat, body_mp4])
        full_mp4 = tmp / "full.mp4"
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "full_list.txt"),
                "-c:v", "libx264", "-preset", "ultrafast", "-an", str(full_mp4))

        subs_ok = whisper_fut.result()

    # ── Fin fase 1 (clip gen) → fase 2 (encode final) ─────────────────────────
    _stop_monitor.set()   # Para el hilo monitor (daemon, muere solo también)
    if progress_cb:
        progress_cb(90.0, 0.0, _total_expected, _total_expected, "encode")

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
            f"fontcolor=white:fontsize=52:box=1:boxcolor=red@0.88:boxborderw=20:"
            f"x=(w-text_w)/2:y=h*0.06:enable='between(t,{cta_start:.1f},{audio_dur:.1f})'"
        )
    else:
        vf = (
            f"{grade},"
            f"drawtext=text='¡SUSCRÍBETE Y ACTIVA LA CAMPANITA!':"
            f"fontcolor=white:fontsize=52:box=1:boxcolor=red@0.88:boxborderw=20:"
            f"x=(w-text_w)/2:y=h*0.06:enable='between(t,{cta_start:.1f},{audio_dur:.1f})'"
        )

    if has_music:
        # Mezcla profesional con DUCKING automático (sidechaincompress):
        # la música baja sola cuando hay narración y sube en los silencios.
        # voice al 100% · música base 0.18 · ducking dispara con la voz.
        _ffmpeg(
            "-i", str(full_mp4),
            "-i", str(audio_path),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex",
            (
                f"[1:a]volume=1.0,asplit=2[voice][sc];"
                f"[2:a]volume=0.18,atrim=0:duration={audio_dur:.2f},"
                f"afade=t=in:st=0:d=4,afade=t=out:st={max(0,audio_dur-4):.1f}:d=4[musicraw];"
                # Ducking: la música (musicraw) se comprime usando la voz (sc) como disparador
                f"[musicraw][sc]sidechaincompress=threshold=0.03:ratio=8:attack=200:release=600[ducked];"
                f"[voice][ducked]amix=inputs=2:duration=first:dropout_transition=3[aout]"
            ),
            "-map", "0:v", "-map", "[aout]",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
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
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
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

    # Short vertical 1080p: 1080×1920 (9:16 Full HD)
    vf_short = (
        "scale=-1:1920,crop=1080:1920,"
        "drawtext=text='¡SUSCRÍBETE!':"
        "fontcolor=white:fontsize=72:box=1:boxcolor=black@0.7:boxborderw=14:"
        "x=(w-text_w)/2:y=h*0.88:"
        f"enable='between(t,{max(0,actual_dur-6):.0f},{actual_dur:.0f})'"
        if total_dur > 120 else
        "scale=-1:1920,crop=1080:1920"
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
                "-vf", "scale=-1:1920,crop=1080:1920",
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
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
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
            "-vf", "scale=-1:1920,crop=1080:1920",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        )


# ── Limpieza de disco ────────────────────────────────────────────────────────

def cleanup_intermediates(job_dir: Path) -> float:
    """
    Tras renderizar: borra archivos de trabajo que ya no se necesitan
    (el vídeo final ya los contiene). Devuelve MB liberados.
    Conserva: final.mp4, short.mp4, thumbnail*.jpg, metadata.json
    """
    freed = 0.0
    targets = [
        job_dir / "narration.mp3",
        job_dir / "images",
        job_dir / "hook_images",
        job_dir / "hook_videos",
        job_dir / "body_videos",
        job_dir / "thumbnail_base.jpg",
    ]
    # Carpetas temporales que pudieran haber quedado de un fallo
    targets += list(job_dir.glob("tmp_*"))
    targets += list(job_dir.glob("_xtts_tmp"))
    targets += list(job_dir.glob("_short_tmp"))

    for t in targets:
        try:
            if not t.exists():
                continue
            if t.is_dir():
                freed += sum(f.stat().st_size for f in t.rglob("*") if f.is_file())
                shutil.rmtree(t)
            else:
                freed += t.stat().st_size
                t.unlink()
        except Exception:
            pass
    return freed / 1024 / 1024


def cleanup_after_upload(job_dir: Path) -> float:
    """
    Tras subir a YouTube: borra TODO lo pesado — el contenido ya está en YouTube.
    Conserva solo: metadata.json, thumbnail.jpg/b/c (registro ligero, ~500KB total).
    Devuelve MB liberados.
    """
    freed = 0.0

    # ── Archivos individuales a borrar ────────────────────────────────────────
    big_files = [
        "final.mp4",       # ~150-250MB — ya está en YouTube
        "short.mp4",       # ~100MB — ya está (o se puede regenerar)
        "narration.mp3",   # ~48MB — se puede regenerar si hace falta
        "narration.raw.mp3",
    ]
    for name in big_files:
        p = job_dir / name
        try:
            if p.exists():
                freed += p.stat().st_size
                p.unlink()
        except Exception:
            pass

    # ── Directorios pesados (imágenes, clips Pexels, temporales) ─────────────
    big_dirs = [
        "images",          # ~8MB — 40 imágenes Flux
        "hook_images",     # ~2MB
        "hook_videos",     # ~150MB — clips Pexels
        "body_videos",     # ~150MB — clips Pexels cuerpo
        "voices",          # temp si se generó algo
    ]
    for name in big_dirs:
        d = job_dir / name
        try:
            if d.exists() and d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file():
                        freed += f.stat().st_size
                shutil.rmtree(d)
        except Exception:
            pass

    # ── Directorios temporales de render ──────────────────────────────────────
    for tmp in job_dir.glob("tmp_*"):
        try:
            if tmp.is_dir():
                for f in tmp.rglob("*"):
                    if f.is_file():
                        freed += f.stat().st_size
                shutil.rmtree(tmp)
        except Exception:
            pass

    freed_mb = freed / 1024 / 1024
    # Log en un archivo pequeño de resumen
    try:
        summary = job_dir / "upload_summary.txt"
        summary.write_text(
            f"Subido a YouTube. {freed_mb:.0f} MB liberados del servidor.\n"
            f"Conservado: metadata.json + thumbnails (~500KB)\n"
        )
    except Exception:
        pass

    return freed_mb


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

        # Agrupar en bloques de 3 palabras, guardando el timing de CADA palabra
        # para poder hacer resaltado karaoke (palabra activa en amarillo).
        groups, chunk = [], []
        for s, e, w in words:
            chunk.append((s, e, w))
            if len(chunk) >= 3:
                groups.append(chunk)
                chunk = []
        if chunk:
            groups.append(chunk)

        ass_path.write_text(_build_ass(groups), encoding="utf-8")
        return True
    except Exception:
        return False


def _build_ass(groups: list) -> str:
    """
    Subtítulos estilo karaoke: la palabra que se está pronunciando se resalta
    en amarillo (PrimaryColour) y las demás quedan en blanco (SecondaryColour),
    usando tags \\k de ASS. Posición elevada para no chocar con los controles
    de YouTube. Pop de entrada con \\fad.
    """
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # PrimaryColour = AMARILLO (palabra activa) · SecondaryColour = BLANCO (resto)
        # Fuente grande, contorno negro grueso, sombra. MarginV=180 = más arriba.
        # 1080p: fuente 90px, contorno 6px, sombra 4px, margen inferior 240px
        "Style: TikTok,Arial Black,90,&H0000F0FF,&H00FFFFFF,&H00000000,&H96000000,"
        "-1,0,0,0,100,100,0.5,0,1,6,4,2,60,60,240,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for group in groups:
        # group = lista de (start, end, word)
        if not group:
            continue
        gs = group[0][0]
        ge = group[-1][1]
        # Construir texto con tags \k (centisegundos por palabra)
        parts = []
        for (ws, we, w) in group:
            w_clean = w.replace("{", "").replace("}", "").replace("\\", "").strip().upper()
            if not w_clean:
                continue
            k_cs = max(1, int((we - ws) * 100))  # duración de la palabra en centiseg
            parts.append(f"{{\\k{k_cs}}}{w_clean} ")
        if not parts:
            continue
        karaoke = "".join(parts).strip()
        # \an2 = abajo-centro · \fad(80,80) = pop de entrada/salida suave
        lines.append(
            f"Dialogue: 0,{_t(gs)},{_t(ge+0.08)},TikTok,,0,0,0,,"
            f"{{\\an2\\fad(80,80)}}{karaoke}"
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
        f"scale=2880:1620,zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)+((iw/zoom/4)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='max(1.3-0.001*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)-((iw/zoom/4)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='min(zoom+0.0008,1.25)':x='iw/2-(iw/zoom/2)':y='ih-(ih/zoom)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)':y='ih/zoom*(0.9-0.4*on/{d})':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='min(zoom+0.001,1.3)':x='iw*0.1':y='ih*0.1':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='(iw/zoom/2)*(0.6+0.8*on/{d})':y='(ih/zoom/2)*(0.6+0.8*on/{d})':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='1.1+0.1*sin(3.14*on/{d})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
        f"scale=2160:1215,zoompan=z=1.2:x='iw/2-(iw/zoom/2)':y='ih/zoom*(0.5+0.4*on/{d})':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='min(zoom+0.001,1.3)':x='iw*0.7':y='ih*0.7':d={d}:s=1920x1080",
        f"scale=2880:1620,zoompan=z='max(1.2-0.0005*on,1.05)':x='iw/2-(iw/zoom/2)+((iw/zoom/6)*on/{d})':y='ih/2-(ih/zoom/2)':d={d}:s=1920x1080",
    ]
    return fx[idx % len(fx)]
