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
- Narración fluida, apta para text-to-speech (sin símbolos raros, sin markdown)
- Gancho inicial en los primeros 60 segundos que sea irresistible
- Estructura: Hook potente → Desarrollo en 5-7 bloques → Cierre con llamada a la acción
- Al final: "Si este vídeo te ha gustado, suscríbete y dale like. Activa la campanita para no perderte nada."
- Devuelve SOLO el guión, sin comentarios extra ni etiquetas"""

def generate_script(job_id: str, niche: str, title: str, tone: str) -> str:
    add_step(job_id, "script", "running", f"Generando guión: {title}")

    tone_desc = TONES.get(tone, TONES["neutro"])
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Nicho: {niche}
Título del vídeo: {title}
Tono del narrador: {tone_desc}

Escribe el guión completo de este vídeo de YouTube. Debe tener entre 3.800 y 4.200 palabras (aprox. 30 minutos de narración a 130 palabras/minuto).

El guión debe empezar directamente con el gancho sin introducción. Solo el texto que leerá el narrador, nada más."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=6000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    script = message.content[0].text

    # Calcular y registrar coste real
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000  # USD
    cost_eur = cost * 0.92

    add_cost_event(job_id, "claude_sonnet", output_tokens, 15 / 1_000_000, cost_eur)
    add_step(job_id, "script", "done", f"Guión generado: {len(script.split())} palabras", cost_eur)

    return script
