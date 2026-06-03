import anthropic
import os
from ..database import add_cost_event, add_step

TONES = {
    "misterio":     "narrador de misterio e intriga, voz grave y pausada, tensión creciente, preguntas retóricas perturbadoras",
    "motivacional": "coach apasionado, energético, frases cortas como golpes, llama a la acción constantemente",
    "documental":   "documentalista profesional, datos precisos y verificados, autoridad, ritmo calmado",
    "drama":        "narrador dramático, emocional, pausas para impacto, protagonistas reales con nombres",
    "humor":        "humorístico pero informativo, anécdotas divertidas, lenguaje cercano y coloquial",
    "neutro":       "narrador profesional, claro, conversacional, fácil de entender",
}

MODEL = "claude-sonnet-4-6"
MODEL_IN_PRICE  = 3    # USD/M tokens input
MODEL_OUT_PRICE = 15   # USD/M tokens output
EUR_RATE = 0.92

TARGET_WORDS = 4200   # ~32 min a 130 palabras/min — sweet spot retención YouTube
MIN_WORDS    = 3800
MAX_WORDS    = 4600

SYSTEM_PROMPT = """Eres el mejor guionista de YouTube en español. Llevas 10 años creando vídeos virales de 30-35 minutos con retención del 60%+.

LEYES ABSOLUTAS:
1. El HOOK decide todo — los primeros 90 segundos son vida o muerte del vídeo
2. Narración oral pura: cero símbolos, cero asteriscos, cero markdown, cero listas. Solo frases que suenan naturales al hablar en voz alta
3. Ritmo dinámico: alterna frases cortas de impacto con frases largas descriptivas. Nunca más de 4 frases seguidas del mismo ritmo
4. Datos precisos: nombres reales, fechas exactas, cifras concretas — dan credibilidad y son más impactantes
5. Muestra, no expliques: recrea escenas, pon diálogos, describe lugares con detalle sensorial
6. Cliffhangers obligatorios al final de cada bloque — el espectador DEBE querer saber qué sigue
7. Sin afirmaciones falsas verificables. Sin contenido inapropiado.
8. Devuelve SOLO el texto narrado, listo para locutar."""


def generate_script(job_id: str, niche: str, title: str, tone: str) -> str:
    add_step(job_id, "script", "running", "Generando guión con Claude Sonnet 4.6…")
    tone_desc = TONES.get(tone, TONES["neutro"])
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_prompt = f"""NICHO: {niche}
TÍTULO: {title}
TONO: {tone_desc}

Escribe el guión COMPLETO. Extensión CRÍTICA: entre {MIN_WORDS} y {MAX_WORDS} palabras.
A 130 palabras/minuto = exactamente 30-35 minutos de vídeo.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOOK (~400 palabras — los primeros 3 minutos son TODO)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Arranca con el hecho más perturbador, impactante o sorprendente del tema. NO te presentes. NO menciones el título. Directo al golpe.

Frase 1: El dato o hecho más impactante, dicho sin rodeos.
Frase 2: Amplifica con un detalle concreto que haga al espectador decir "¿qué?"
Frase 3-5: Desarrolla brevemente el contexto que hace ese hecho aún más perturbador.

Luego: Una pregunta retórica que conecte con el espectador emocionalmente.
Después: Promesa de lo que va a descubrir ("En los próximos minutos vas a entender por qué esto cambia todo lo que creías saber sobre [tema].")
Cierra el hook con: una imagen mental tan vívida que sea imposible no seguir escuchando.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BLOQUE 1 (~550 palabras): El origen — la historia que no te contaron
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Recrea el escenario con detalles sensoriales: lugar, época, personas. Usa un protagonista específico con nombre. Recrea una escena como si fuera una película. Datos verificables que sorprendan.
Cliffhanger final: algo que contradice lo que el espectador acaba de asumir.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BLOQUE 2 (~550 palabras): La verdad que ocultaron
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
El giro. La versión oficial vs. lo que realmente pasó. Documentos, testimonios, fechas. Tensión creciente.
Cliffhanger: "Y sin embargo, lo que viene a continuación es todavía más difícil de creer."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BLOQUE 3 (~550 palabras): Los casos reales — las personas detrás de la historia
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Humaniza con casos concretos. Nombres, fechas, consecuencias reales en vidas reales. Mínimo 2 historias individuales detalladas.
Cliffhanger con el dato más perturbador del bloque.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BLOQUE 4 (~550 palabras): Lo que los expertos saben y callan
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Citas de especialistas, estudios, informes. Contradicción entre fuentes oficiales y evidencias. Sube la tensión al máximo.
Cliffhanger: "Pero nada de esto explica lo que vas a escuchar ahora. Nada."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BLOQUE 5 (~550 palabras): La revelación — el punto más alto
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
El momento cumbre del vídeo. La información que el espectador jamás ha escuchado. Recréalo con máximo dramatismo. Pausa. Deja que el impacto aterrice.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BLOQUE 6 (~500 palabras): Las consecuencias — qué significa esto HOY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Conecta con el presente. Cómo afecta directamente al espectador. Reflexión que cambia la perspectiva. Empodera o perturba, pero siempre con impacto emocional.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CIERRE (~100 palabras)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Vuelve a la imagen del hook inicial, cerrando el círculo narrativo. Una última reflexión que deje al espectador pensando.
Termina EXACTAMENTE con esta frase: "Si este vídeo te ha abierto los ojos, suscríbete y dale like. Activa la campanita para no perderte nada. Nos vemos en el próximo vídeo."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPORTANTE: SOLO el texto narrado. Sin títulos de sección. Sin corchetes. Sin numeración. Sin acotaciones. Solo las palabras que va a decir el narrador."""

    msg = client.messages.create(
        model=MODEL,
        max_tokens=9000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    script = msg.content[0].text.strip()
    word_count = len(script.split())

    cost_eur = (msg.usage.input_tokens * MODEL_IN_PRICE +
                msg.usage.output_tokens * MODEL_OUT_PRICE) / 1_000_000 * EUR_RATE
    add_cost_event(job_id, "claude_sonnet", msg.usage.output_tokens,
                   MODEL_OUT_PRICE / 1_000_000, cost_eur)

    # Extensión si sale corto
    if word_count < MIN_WORDS:
        add_step(job_id, "script", "running",
                 f"Guión corto ({word_count} palabras), extendiendo…")
        ext_msg = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": (
                f"El guión tiene {word_count} palabras, necesito {TARGET_WORDS - word_count} más. "
                f"Amplía los bloques más flojos con más detalle narrativo, datos concretos y escenas recreadas. "
                f"Mismo tono: {tone_desc}. SOLO el texto adicional.\n\n"
                f"Continúa desde: ...{script[-400:]}"
            )}]
        )
        script = script + "\n\n" + ext_msg.content[0].text.strip()
        word_count = len(script.split())
        ext_cost = (ext_msg.usage.input_tokens * MODEL_IN_PRICE +
                    ext_msg.usage.output_tokens * MODEL_OUT_PRICE) / 1_000_000 * EUR_RATE
        add_cost_event(job_id, "claude_sonnet_ext", ext_msg.usage.output_tokens,
                       MODEL_OUT_PRICE / 1_000_000, ext_cost)

    estimated_mins = round(word_count / 130)
    add_step(job_id, "script", "done",
             f"Guión: {word_count} palabras (~{estimated_mins} min) — {cost_eur:.4f}€",
             cost_eur)
    return script
