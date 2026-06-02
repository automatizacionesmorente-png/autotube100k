import anthropic
import os
from ..database import add_cost_event, add_step

TONES = {
    "misterio": "narrador de misterio e intriga, voz grave y pausada, crea tensión constante",
    "motivacional": "coach motivacional apasionado, energético, usa frases cortas y poderosas",
    "documental": "documentalista profesional, objetivo, datos precisos, ritmo calmado",
    "drama": "narrador dramático, emocional, usa pausas para impacto máximo",
    "humor": "humorístico pero informativo, anécdotas divertidas, lenguaje cercano",
    "neutro": "narrador profesional claro y directo, fácil de entender",
}

SYSTEM_PROMPT = """Eres un guionista experto en vídeos virales de YouTube en español.
Tu objetivo: escribir guiones que mantengan al espectador pegado durante 30+ minutos.

REGLAS ABSOLUTAS:
- Sin palabrotas ni lenguaje inapropiado
- Sin afirmaciones falsas o desinformación
- Narración fluida, apta para text-to-speech (sin símbolos raros, sin markdown, sin asteriscos, sin guiones de lista)
- GANCHO INICIAL IRRESISTIBLE: los primeros 60 segundos deben ser tan impactantes que sea imposible cerrar el vídeo
- Estructura obligatoria: Hook brutal (300 palabras) → 6 bloques de desarrollo (600 palabras cada uno) → Cierre con CTA
- Cada bloque debe terminar con un gancho hacia el siguiente ("Y lo que viene ahora es todavía más impactante...")
- Al final exacto: "Si este vídeo te ha gustado, suscríbete y dale like. Activa la campanita para no perderte nada."
- Devuelve SOLO el texto que leerá el narrador, sin títulos de sección, sin comentarios, sin markdown"""

TARGET_WORDS = 4200  # ~32 min a 130 wpm
MIN_WORDS = 3900


def generate_script(job_id: str, niche: str, title: str, tone: str) -> str:
    add_step(job_id, "script", "running", f"Generando guión: {title}")

    tone_desc = TONES.get(tone, TONES["neutro"])
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Nicho: {niche}
Título del vídeo: {title}
Tono del narrador: {tone_desc}

Escribe el guión completo para este vídeo de YouTube de 30 minutos.
REQUISITO CRÍTICO: El guión DEBE tener exactamente entre {TARGET_WORDS-200} y {TARGET_WORDS+300} palabras.
Eso son aproximadamente 30-33 minutos de narración a 130 palabras/minuto.

Estructura obligatoria:
1. HOOK (primeros 60 segundos, ~300 palabras): Empieza con una pregunta o afirmación que deje al espectador en shock. Sin presentaciones. Directo al impacto.
2. BLOQUE 1 (~600 palabras): Primer gran tema. Termina con un cliffhanger.
3. BLOQUE 2 (~600 palabras): Segundo tema. Más profundidad. Más tensión.
4. BLOQUE 3 (~600 palabras): Giro inesperado o revelación impactante.
5. BLOQUE 4 (~600 palabras): Datos o historia que nadie esperaba.
6. BLOQUE 5 (~600 palabras): El momento más impactante del vídeo.
7. BLOQUE 6 (~600 palabras): Reflexión final y conclusión poderosa.
8. CIERRE (~100 palabras): Llamada a la acción + suscripción.

SOLO el texto narrado. Sin títulos, sin corchetes, sin markdown."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,  # aumentado para asegurar guión completo
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    script = message.content[0].text
    word_count = len(script.split())

    # Si el guión es demasiado corto, pedir una extensión
    if word_count < MIN_WORDS:
        add_step(job_id, "script", "running",
                 f"Guión corto ({word_count} palabras), ampliando hasta {TARGET_WORDS}...")
        extension_needed = TARGET_WORDS - word_count
        ext_msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": script},
                {"role": "user", "content": (
                    f"El guión tiene {word_count} palabras y necesita {TARGET_WORDS}. "
                    f"Añade aproximadamente {extension_needed} palabras más desarrollando los bloques existentes "
                    f"con más detalle, ejemplos y anécdotas. Devuelve SOLO el guión completo y ampliado, sin comentarios."
                )}
            ]
        )
        script = ext_msg.content[0].text
        word_count = len(script.split())
        # Sumar coste de la extensión
        ext_cost = (ext_msg.usage.input_tokens * 3 + ext_msg.usage.output_tokens * 15) / 1_000_000 * 0.92
        add_cost_event(job_id, "claude_sonnet_ext", ext_msg.usage.output_tokens, 15 / 1_000_000, ext_cost)

    # Registrar coste principal
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost_eur = (input_tokens * 3 + output_tokens * 15) / 1_000_000 * 0.92

    add_cost_event(job_id, "claude_sonnet", output_tokens, 15 / 1_000_000, cost_eur)
    estimated_mins = round(word_count / 130)
    add_step(job_id, "script", "done",
             f"Guión listo: {word_count} palabras (~{estimated_mins} min)", cost_eur)

    return script
