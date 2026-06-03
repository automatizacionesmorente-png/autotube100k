import os
import httpx
import anthropic
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from ..database import add_cost_event, add_step

FAL_SYNC      = "https://fal.run/fal-ai/flux/schnell"
FAL_IDEOGRAM  = "https://fal.run/fal-ai/ideogram/v2/turbo"
FAL_UNIT_PRICE_USD      = 0.003   # Flux Schnell por imagen
FAL_IDEOGRAM_PRICE_USD  = 0.05    # Ideogram v2 Turbo por imagen
EUR_RATE = 0.92

FLUX_COUNT = 40    # TODOS los frames del cuerpo con Flux Schnell — sin Pollinations
TOTAL_COUNT = 40
HOOK_COUNT = 8     # 8 imágenes de impacto para el hook


# ── Prompts de imagen — NARRATIVOS (cada imagen = momento exacto del guión) ───

def generate_image_prompts(job_id: str, script: str, niche: str,
                            count: int = TOTAL_COUNT) -> list[str]:
    """
    Divide el guión en `count` segmentos y genera un prompt visual para cada uno.
    Cada imagen corresponde exactamente a lo que se está narrando en ese momento.
    """
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        # Dividir guión en segmentos proporcionales
        words = script.split()
        seg_size = max(1, len(words) // count)
        segments = []
        for i in range(count):
            start = i * seg_size
            end = start + seg_size if i < count - 1 else len(words)
            seg = " ".join(words[start:end])
            segments.append(seg[:300])  # max 300 chars por segmento al prompt

        segments_text = "\n".join(
            f"[{i+1}] {seg}" for i, seg in enumerate(segments)
        )

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5000,
            messages=[{"role": "user", "content": f"""Eres un director de fotografía de documentales. Para cada fragmento de guión, crea el prompt de imagen AI que capture VISUALMENTE y con PRECISIÓN lo que se está narrando.

NICHO: {niche}

REGLAS ESTRICTAS:
- Cada prompt debe reflejar EL CONTENIDO ESPECÍFICO de ese fragmento (lugares reales, objetos, acciones, atmósferas descritas)
- Si habla de una ciudad: pon esa ciudad concreta
- Si habla de una persona: silhouette o primer plano de manos/ojos (sin rostro reconocible)
- Si habla de un documento/carta/carta: close-up del objeto
- Si es un momento de tensión: oscuro, dramático, expresivo
- Si es informativo: documental natural, periodístico
- Varía el tipo de plano: gran angular, primer plano, aéreo, detalle, conceptual
- Siempre: photorealistic, 16:9, 4K, sin texto ni logos

Devuelve SOLO {count} prompts en inglés, uno por línea, sin numeración ni texto extra.

FRAGMENTOS DEL GUIÓN:
{segments_text}"""}]
        )

        prompts = [p.strip() for p in msg.content[0].text.strip().split("\n")
                   if p.strip()][:count]
        while len(prompts) < count:
            prompts.append(
                f"cinematic documentary scene, {niche}, dramatic lighting, 16:9, 4K, photorealistic"
            )

        cost = (msg.usage.input_tokens * 0.8 + msg.usage.output_tokens * 4) / 1_000_000 * EUR_RATE
        add_cost_event(job_id, "claude_haiku_prompts", msg.usage.output_tokens, 4/1_000_000, cost)
        return prompts

    except Exception:
        return _fallback_prompts(niche, count)


def generate_hook_prompts(niche: str, script_hook: str) -> list[str]:
    """
    8 prompts visuales para el hook generados a partir del texto real del guión.
    Cada imagen captura una escena concreta mencionada en el hook.
    """
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": f"""Eres un director de fotografía. Lee este fragmento de guión y genera 8 prompts de imagen AI que capturen visualmente las escenas, lugares, personas o momentos ESPECÍFICOS mencionados.

NICHO: {niche}
FRAGMENTO DEL GUIÓN (hook):
{script_hook[:600]}

REGLAS:
- Cada prompt refleja algo CONCRETO del texto (lugar, objeto, persona, momento)
- Estilo: cinematic, photorealistic, dramatic lighting, 16:9, 4K
- Sin texto, sin logos, sin rostros reconocibles (silhouettes si hay personas)
- Varía los planos: aerial, close-up, wide, detail
- Máximo dramático e impactante

Devuelve SOLO 8 prompts en inglés, uno por línea, sin numeración."""}]
        )
        prompts = [p.strip() for p in msg.content[0].text.strip().split("\n") if p.strip()][:8]
        while len(prompts) < 8:
            prompts.append(
                f"dramatic cinematic scene {niche}, dark moody lighting, photorealistic, 4K, 16:9"
            )
        return prompts
    except Exception:
        # fallback genérico si falla Haiku
        return [
            f"extreme close-up human eyes wide open shock, {niche}, cinematic chiaroscuro, 4K",
            f"dramatic dark corridor single beam of light, {niche}, heavy fog, cinematic, 4K",
            f"aerial shot city at night rain neon reflections, {niche}, dark moody, cinematic",
            f"silhouette lone figure stormy sky lightning, {niche}, backlit, wide shot, 4K",
            f"close-up trembling hands holding document, {niche}, dramatic side light, 4K",
            f"broken mirror reflection symbolic truth, {niche}, dark moody, cinematic, 4K",
            f"old classified files documents secrets, {niche}, dramatic spotlight, photorealistic",
            f"dramatic newspaper headline depth of field city, {niche}, desaturated, cinematic",
        ]


def _fallback_prompts(niche: str, count: int) -> list[str]:
    base = [
        f"cinematic wide dramatic sky at night, {niche}, epic colors, 16:9, 4K",
        f"dramatic close-up hands evidence clue, {niche}, studio lighting, bokeh",
        f"aerial drone city night lights fog, {niche}, neon, 4K, cinematic",
        f"documentary natural scene investigation, {niche}, soft light, photorealistic",
        f"dark abstract concept mystery, {niche}, deep shadows, 4K",
        f"silhouette figure dramatic landscape, {niche}, golden hour, 16:9",
        f"extreme close-up eyes reflection truth, {niche}, warm light, photorealistic",
        f"abandoned building interior mystery, {niche}, chiaroscuro lighting",
        f"close-up old documents files secrets, {niche}, dramatic light, photorealistic",
        f"night street rain reflections lonely, {niche}, blue tones, cinematic, 4K",
    ]
    return (base * 5)[:count]


# ── Generación de imágenes — TODO Flux Schnell ────────────────────────────────

def _fal(client: httpx.Client, prompt: str, key: str) -> tuple[str, float]:
    r = client.post(
        FAL_SYNC,
        headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
        json={"prompt": prompt, "image_size": "landscape_16_9", "num_images": 1},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"FAL {r.status_code}: {r.text[:200]}")
    data = r.json()
    imgs = data.get("images") or data.get("output", {}).get("images", [])
    if not imgs:
        raise RuntimeError("FAL: sin imágenes")
    units = float(r.headers.get("x-fal-billable-units", "1"))
    cost_eur = units * FAL_UNIT_PRICE_USD * EUR_RATE
    return imgs[0]["url"], cost_eur


def _pollinations(client: httpx.Client, prompt: str, out: Path) -> Path | None:
    """Fallback gratuito si fal.ai falla."""
    try:
        safe = prompt[:220].replace(" ", "%20").replace(",", "%2C").replace(":", "%3A").replace("'", "")
        url = f"https://image.pollinations.ai/prompt/{safe}?width=1280&height=720&nologo=true&model=flux"
        r = client.get(url, timeout=50, follow_redirects=True)
        if r.status_code == 200 and len(r.content) > 8000:
            out.write_bytes(r.content)
            return out
    except Exception:
        pass
    return None


def _download(client: httpx.Client, url: str, out: Path) -> Path:
    r = client.get(url, timeout=60)
    r.raise_for_status()
    out.write_bytes(r.content)
    return out


def generate_images(job_id: str, prompts: list[str], output_dir: Path,
                    on_progress=None) -> list[Path]:
    """Genera TODAS las imágenes con Flux Schnell. Pollinations solo como fallback."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    add_step(job_id, "images", "running",
             f"Generando {len(prompts)} imágenes con Flux Schnell (3 concurrentes)…")
    output_dir.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("FAL_API_KEY", "")

    paths: list[Path | None] = [None] * len(prompts)
    fal_cost_total = 0.0
    lock = threading.Lock()
    done_count = 0

    def _process_one(i: int, prompt: str):
        nonlocal fal_cost_total, done_count
        out = output_dir / f"img_{i:03d}.jpg"
        result = None
        with httpx.Client(timeout=200) as client:
            if key:
                try:
                    url, cost_eur = _fal(client, prompt, key)
                    _download(client, url, out)
                    result = out
                    with lock:
                        fal_cost_total += cost_eur
                    add_cost_event(job_id, "fal_flux_schnell", 1,
                                   FAL_UNIT_PRICE_USD, round(cost_eur, 6))
                except Exception:
                    result = _pollinations(client, prompt, out)
            else:
                result = _pollinations(client, prompt, out)

        with lock:
            paths[i] = result
            done_count += 1
            current_done = done_count
            current_cost = fal_cost_total

        if on_progress:
            on_progress(current_done, len(prompts), current_cost)
        elif current_done % 5 == 0:
            add_step(job_id, "images", "running",
                     f"Imágenes: {current_done}/{len(prompts)} · fal.ai: {current_cost:.4f}€")

    # 3 workers concurrentes — fal.ai lo soporta bien
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_process_one, i, prompts[i]): i for i in range(len(prompts))}
        for f in futs:
            f.result()

    valid = [p for p in paths if p]
    add_step(job_id, "images", "done",
             f"{len(valid)}/{len(prompts)} imágenes · fal.ai: {fal_cost_total:.4f}€",
             fal_cost_total)
    return valid


def generate_hook_images(job_id: str, niche: str, script_hook: str,
                          output_dir: Path) -> list[Path]:
    """8 imágenes de alta calidad para el hook — siempre Flux Schnell."""
    add_step(job_id, "hook_images", "running",
             f"Generando {HOOK_COUNT} imágenes de impacto para el hook…")
    output_dir.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("FAL_API_KEY", "")
    prompts = generate_hook_prompts(niche, script_hook)
    paths = []
    fal_cost = 0.0

    with httpx.Client(timeout=200) as client:
        for i, prompt in enumerate(prompts):
            out = output_dir / f"hook_{i:02d}.jpg"
            if key:
                try:
                    url, cost_eur = _fal(client, prompt, key)
                    _download(client, url, out)
                    paths.append(out)
                    fal_cost += cost_eur
                    add_cost_event(job_id, "fal_flux_hook", 1,
                                   FAL_UNIT_PRICE_USD, round(cost_eur, 6))
                    continue
                except Exception:
                    pass
            p = _pollinations(client, prompt, out)
            if p:
                paths.append(p)

    add_step(job_id, "hook_images", "done",
             f"{len(paths)} imágenes hook · fal.ai: {fal_cost:.4f}€", fal_cost)
    return paths


# Queries Pexels por tono — busca caras humanas reales con la emoción correcta
THUMB_PEXELS_QUERY = {
    "misterio":     "shocked surprised man dark dramatic portrait",
    "drama":        "sad emotional woman dramatic portrait close up",
    "motivacional": "confident powerful man portrait determination",
    "documental":   "serious professional woman documentary portrait",
    "humor":        "funny surprised face laughing portrait",
    "neutro":       "serious man portrait dramatic lighting",
    "truecrime":    "worried anxious woman dark portrait",
    "historia":     "dramatic man historical serious portrait",
    "conspiracion": "suspicious paranoid man dark portrait shadows",
    "ciencia":      "amazed scientist woman discovery portrait",
}


def _thumbnail_headline(title: str, niche: str, tone: str) -> str:
    """
    Genera un titular MUY corto (2-4 palabras) de máxima curiosidad para la
    miniatura, distinto del título. Es lo que más sube el CTR.
    Reglas de oro de miniaturas virales:
    - 2-4 palabras grandes y legibles en móvil
    - genera una pregunta/incógnita en el cerebro (curiosity gap)
    - complementa al título, no lo repite
    """
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content": f"""Vídeo de YouTube: "{title}" (nicho: {niche}).
Dame SOLO un titular de MINIATURA de 2 a 4 palabras EN MAYÚSCULAS, en español,
que genere curiosidad extrema y dé ganas de hacer clic. NO repitas el título.
Que sea legible en un móvil pequeño. Sin comillas, sin explicación. Solo el texto."""}]
        )
        headline = msg.content[0].text.strip().strip('"').upper()
        # Seguridad: máximo 4 palabras
        words = headline.split()
        if 1 <= len(words) <= 5:
            return " ".join(words[:4])
    except Exception:
        pass
    # Fallback: palabras clave del título
    skip = {"DE","DEL","LA","EL","LOS","LAS","UN","UNA","QUE","POR","EN","CON","SIN","Y","O","A","AL"}
    words = [w for w in title.upper().split() if w not in skip]
    return " ".join(words[:3]) if words else title.upper()[:20]


def generate_thumbnail(job_id: str, title: str, niche: str, output_dir: Path,
                        tone: str = "neutro") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final   = output_dir / "thumbnail.jpg"
    base    = output_dir / "thumbnail_base.jpg"
    fal_key = os.environ.get("FAL_API_KEY", "")
    pexels_key = os.environ.get("PEXELS_API_KEY", "")

    # Titular viral corto — se usa en TODAS las variantes (caras reales incluidas)
    headline = _thumbnail_headline(title, niche, tone)

    # ── Opción A: Cara humana real de Pexels + PIL (GRATIS, aspecto 100% humano) ─
    if pexels_key:
        try:
            add_step(job_id, "thumbnail", "running",
                     "Buscando cara humana real en Pexels para miniatura…")
            with httpx.Client(timeout=30) as client:
                face = _pexels_portrait(client, pexels_key, tone, niche)
            if face:
                face.save(str(base), "JPEG", quality=95)
                _compose_thumbnail(base, title, tone, final, headline=headline)
                _variant_red(base, headline, output_dir / "thumbnail_b.jpg")
                _variant_minimal(base, headline, output_dir / "thumbnail_c.jpg")
                add_step(job_id, "thumbnail", "done",
                         f"Miniatura cara real + titular '{headline}' · 0.00€", 0)
                return final
        except Exception as e:
            add_step(job_id, "thumbnail", "running",
                     f"Pexels falló ({str(e)[:50]}), usando Ideogram…")

    # ── Opción B: Ideogram v2 Turbo (genera texto integrado, $0.05) ──────────────
    if fal_key:
        try:
            add_step(job_id, "thumbnail", "running",
                     "Generando miniatura con Ideogram v2 Turbo…")
            thumb_text, ideogram_prompt = _build_ideogram_prompt(job_id, title, niche, tone)
            with httpx.Client(timeout=180) as client:
                r = client.post(
                    FAL_IDEOGRAM,
                    headers={"Authorization": f"Key {fal_key}",
                             "Content-Type": "application/json"},
                    json={"prompt": ideogram_prompt, "aspect_ratio": "16:9",
                          "style_type": "DESIGN", "magic_prompt_option": "OFF"},
                    timeout=120,
                )
                if r.status_code == 200:
                    imgs = r.json().get("images", [])
                    if imgs:
                        _download(client, imgs[0]["url"], base)
                        cost_eur = FAL_IDEOGRAM_PRICE_USD * EUR_RATE
                        add_cost_event(job_id, "ideogram_thumbnail", 1,
                                       FAL_IDEOGRAM_PRICE_USD, round(cost_eur, 6))
                        # Ideogram ya integra el texto en la imagen — no recomponer
                        import shutil as _sh
                        _sh.copy2(base, final)
                        _variant_red(base, headline, output_dir / "thumbnail_b.jpg")
                        _variant_minimal(base, headline, output_dir / "thumbnail_c.jpg")
                        add_step(job_id, "thumbnail", "done",
                                 f"Miniatura Ideogram · {cost_eur:.4f}€", cost_eur)
                        return final
        except Exception:
            pass

    # ── Opción C: Flux Schnell + PIL (fallback final) ─────────────────────────────
    add_step(job_id, "thumbnail", "running", "Fallback: Flux Schnell + PIL…")
    prompt_flux = (
        f"dramatic cinematic portrait face shocked expression, {niche}, "
        "dark moody background, studio lighting, high contrast, 16:9, photorealistic, no text"
    )
    with httpx.Client(timeout=200) as client:
        if fal_key:
            try:
                url, cost_eur = _fal(client, prompt_flux, fal_key)
                _download(client, url, base)
                add_cost_event(job_id, "fal_flux_thumbnail", 1, FAL_UNIT_PRICE_USD, round(cost_eur, 6))
            except Exception:
                _pollinations(client, prompt_flux, base)
        else:
            _pollinations(client, prompt_flux, base)

    if base.exists():
        _compose_thumbnail(base, title, tone, final, headline=headline)
        _variant_red(base, headline, output_dir / "thumbnail_b.jpg")
        _variant_minimal(base, headline, output_dir / "thumbnail_c.jpg")
    else:
        _solid_thumbnail(title, final)

    add_step(job_id, "thumbnail", "done", "Miniatura (fallback PIL) lista", 0)
    return final


def _pexels_portrait(client: httpx.Client, api_key: str, tone: str, niche: str):
    """Busca una foto de persona real en Pexels. Devuelve imagen PIL o None."""
    query = THUMB_PEXELS_QUERY.get(tone, f"dramatic portrait {niche}")
    r = client.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": api_key},
        params={"query": query, "per_page": 8, "orientation": "landscape"},
        timeout=20,
    )
    if r.status_code != 200:
        return None
    photos = r.json().get("photos", [])
    if not photos:
        return None

    import random
    from PIL import Image as PILImage
    import io
    photo = random.choice(photos[:5])
    img_url = photo["src"].get("large2x") or photo["src"]["large"]
    img_resp = client.get(img_url, timeout=30)
    img_resp.raise_for_status()
    img = PILImage.open(io.BytesIO(img_resp.content)).convert("RGB")
    # Recortar a 16:9
    w, h = img.size
    target_h = int(w * 9 / 16)
    if target_h <= h:
        top = (h - target_h) // 2
        img = img.crop((0, top, w, top + target_h))
    img = img.resize((1280, 720), PILImage.LANCZOS)
    return img


def _compose_thumbnail(base: Path, title: str, tone: str, out: Path, headline: str = None):
    """
    Compone la miniatura profesional:
    - Gradiente oscuro izquierda (donde va el texto)
    - Texto grande, bold, blanco con sombra negra gruesa
    - Acento de color según el tono
    Si se pasa `headline`, se usa ese titular corto viral en vez del título.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.open(base).convert("RGB").resize((1280, 720), Image.LANCZOS)
        overlay = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
        ov = ImageDraw.Draw(overlay)

        # Gradiente izquierdo (55% del ancho) para que el texto sea legible
        for x in range(700):
            alpha = int(210 * max(0, 1 - (x / 650) ** 0.6))
            ov.line([(x, 0), (x, 720)], fill=(0, 0, 0, alpha))

        # Borde inferior oscuro
        for y in range(600, 720):
            alpha = int(160 * ((y - 600) / 120))
            ov.line([(0, y), (1280, y)], fill=(0, 0, 0, alpha))

        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Color de acento por tono
        accent = {
            "misterio": (100, 140, 255), "drama": (200, 60, 60),
            "motivacional": (255, 180, 0), "documental": (60, 180, 120),
            "humor": (255, 120, 0), "neutro": (200, 200, 200),
        }.get(tone, (255, 255, 255))

        # Barra de acento vertical izquierda
        draw.rectangle([(0, 0), (10, 720)], fill=accent)

        # Usar el titular viral corto si está disponible; si no, palabras del título
        if headline:
            imp_words = headline.upper().split()
        else:
            words = title.upper().split()
            skip = {"DE", "DEL", "LA", "EL", "LOS", "LAS", "UN", "UNA", "QUE",
                     "POR", "EN", "CON", "SIN", "Y", "O", "A", "AL"}
            imp_words = [w for w in words if w not in skip] or words
        lines, cur = [], ""
        for w in imp_words:
            test = (cur + " " + w).strip()
            if len(test) <= 14:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        lines = lines[:3]

        fsize = 96 if len(lines) == 1 else (80 if len(lines) == 2 else 66)
        font_big  = _load_font(fsize)
        font_sub  = _load_font(28)

        total_h = len(lines) * (fsize + 8)
        y = (720 - total_h) // 2 - 20

        for line in lines:
            x = 28
            # Sombra múltiple para máximo contraste
            for dx, dy in [(-4,-4),(4,-4),(-4,4),(4,4),(0,-4),(0,4),(-4,0),(4,0)]:
                draw.text((x+dx, y+dy), line, font=font_big, fill=(0,0,0,220))
            draw.text((x, y), line, font=font_big, fill=(255,255,255))
            y += fsize + 8

        # Sub-texto con el nicho/canal
        draw.text((28, y + 8), "▶ MÁS EN EL CANAL", font=font_sub, fill=(*accent, 200))

        img.save(out, "JPEG", quality=95)
    except Exception:
        import shutil
        shutil.copy2(base, out)


def _build_ideogram_prompt(job_id: str, title: str, niche: str, tone: str) -> tuple[str, str]:
    """Usa Haiku para generar texto corto + prompt Ideogram optimizado."""
    try:
        client_ai = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client_ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": f"""Eres un diseñador de miniaturas virales de YouTube.
Para el vídeo "{title}" sobre "{niche}", genera:

1. TEXTO_MINIATURA: 3-5 palabras en mayúsculas, impactante, que genere curiosidad extrema (NO el título completo)
2. PROMPT_IDEOGRAM: prompt en inglés para Ideogram AI que genere una miniatura YouTube espectacular con ese texto integrado

El prompt debe incluir:
- El texto exacto entre comillas
- Una cara humana con expresión de shock/terror/asombro (sin rostro reconocible si es posible)
- Estilo visual según tono "{tone}": {'oscuro azul frío dramático' if tone in ('misterio','drama') else 'energético cálido' if tone == 'motivacional' else 'cinematográfico profesional'}
- Alta resolución, colores muy saturados, contraste extremo
- Estilo YouTube thumbnail profesional

Responde EXACTAMENTE así:
TEXTO: [texto]
PROMPT: [prompt en inglés]"""}]
        )
        text = msg.content[0].text.strip()
        lines = {line.split(":")[0].strip(): ":".join(line.split(":")[1:]).strip()
                 for line in text.split("\n") if ":" in line}
        thumb_text = lines.get("TEXTO", title[:30].upper())
        prompt = lines.get("PROMPT", "")
        if not prompt:
            raise ValueError("no prompt")
        cost = (msg.usage.input_tokens * 0.8 + msg.usage.output_tokens * 4) / 1_000_000 * EUR_RATE
        add_cost_event(job_id, "claude_haiku_thumb", msg.usage.output_tokens, 4/1_000_000, cost)
        return thumb_text, prompt
    except Exception:
        # Fallback manual
        words = title.upper().split()
        thumb_text = " ".join(words[:4])
        prompt = (
            f'YouTube thumbnail, bold white text "{thumb_text}" with red outline, '
            f"shocked human face expression, {niche} concept, dark dramatic background, "
            f"red and black color scheme, extreme contrast, professional graphic design, "
            f"clickbait viral style, high saturation, cinematic lighting"
        )
        return thumb_text, prompt


# ── Thumbnail con texto ────────────────────────────────────────────────────────

def _add_title_text(base: Path, title: str, out: Path):
    try:
        img = Image.open(base).convert("RGB").resize((1280, 720), Image.LANCZOS)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ov = ImageDraw.Draw(overlay)
        for y in range(350, 720):
            alpha = int(200 * (y - 350) / 370)
            ov.line([(0, y), (1280, y)], fill=(0, 0, 0, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        words = title.upper().split()
        lines, cur = [], ""
        for w in words:
            test = (cur + " " + w).strip()
            if len(test) <= 26:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        lines = lines[:3]
        fsize = 82 if len(lines) == 1 else (70 if len(lines) == 2 else 58)
        font = _load_font(fsize)
        total_h = len(lines) * (fsize + 10)
        y = 720 - total_h - 30
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (1280 - (bbox[2] - bbox[0])) // 2
            draw.text((x+3, y+3), line, fill=(0, 0, 0, 200), font=font)
            draw.text((x, y), line, fill=(255, 255, 255), font=font)
            y += fsize + 10
        img.save(out, "JPEG", quality=93)
    except Exception:
        import shutil
        shutil.copy2(base, out)


def _solid_thumbnail(title: str, out: Path):
    img = Image.new("RGB", (1280, 720))
    draw = ImageDraw.Draw(img)
    for y in range(720):
        r = int(10 + 160 * y / 720)
        draw.line([(0, y), (1280, y)], fill=(r, 10, 30))
    font = _load_font(72)
    words = title.upper().split()
    lines, cur = [], ""
    for w in words:
        if len((cur + " " + w).strip()) <= 18:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    total_h = len(lines) * 86
    y = (720 - total_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (1280 - (bbox[2] - bbox[0])) // 2
        draw.text((x+3, y+3), line, fill=(0, 0, 0), font=font)
        draw.text((x, y), line, fill=(255, 215, 0), font=font)
        y += 86
    img.save(out, "JPEG", quality=93)


def _load_font(size: int):
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _title_lines(title: str, max_chars: int = 22) -> list[str]:
    """Divide el título en líneas cortas para miniatura."""
    words = title.upper().split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if len(test) <= max_chars:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def _variant_red(base: Path, title: str, out: Path):
    """Variante B: fondo oscuro + texto rojo brillante + barra lateral roja."""
    try:
        img = Image.open(base).convert("RGB").resize((1280, 720), Image.LANCZOS)
        # Oscurecer imagen base
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 140))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Barra lateral roja izquierda
        draw.rectangle([(0, 0), (18, 720)], fill=(220, 30, 30))

        lines = _title_lines(title, 18)
        fsize = 88 if len(lines) == 1 else (72 if len(lines) == 2 else 60)
        font = _load_font(fsize)
        y = 720 // 2 - (len(lines) * (fsize + 8)) // 2
        for line in lines:
            # Sombra
            draw.text((42, y + 4), line, font=font, fill=(0, 0, 0))
            # Texto rojo brillante
            draw.text((40, y), line, font=font, fill=(255, 50, 50))
            y += fsize + 8
        img.save(out, "JPEG", quality=93)
    except Exception:
        pass


def _variant_minimal(base: Path, title: str, out: Path):
    """Variante C: franja negra inferior grande + texto blanco enorme — máximo impacto texto."""
    try:
        img = Image.open(base).convert("RGB").resize((1280, 720), Image.LANCZOS)
        draw = ImageDraw.Draw(img)

        lines = _title_lines(title, 20)
        fsize = 90 if len(lines) == 1 else (76 if len(lines) == 2 else 62)
        font = _load_font(fsize)
        total_h = len(lines) * (fsize + 10) + 40

        # Franja negra semitransparente en la parte inferior
        bar_top = 720 - total_h - 20
        bar_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        bar_draw = ImageDraw.Draw(bar_overlay)
        bar_draw.rectangle([(0, bar_top), (1280, 720)], fill=(0, 0, 0, 200))
        img = Image.alpha_composite(img.convert("RGBA"), bar_overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        y = bar_top + 20
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (1280 - (bbox[2] - bbox[0])) // 2
            # Stroke
            for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))
            y += fsize + 10
        img.save(out, "JPEG", quality=93)
    except Exception:
        pass
