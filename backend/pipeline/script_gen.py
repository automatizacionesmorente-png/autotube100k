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

SYSTEM_PROMPT = """Eres el mejor guionista de YouTube en español. Llevas 10 años creando vídeos virales de 30-35 minutos con retención del 65%+. Tus vídeos han acumulado más de 500 millones de visualizaciones.

LEYES ABSOLUTAS — VIOLARIAS ES UN FRACASO:

1. EL LOOP PROHIBIDO (obligatorio en el hook):
   Empieza con el momento MÁS impactante del vídeo — la revelación final, el giro, la fecha exacta del desastre.
   Luego di: "Pero para entender cómo llegamos a este punto, necesito contarte algo que ocurrió [X años antes]..."
   Esto crea un bucle psicológico: el espectador TIENE que quedarse para ver cómo se llega al momento inicial.

2. TEASER DE MITAD DE VÍDEO (en el bloque 2 o 3):
   Lanza una promesa: "Y antes de que acabe este vídeo, voy a revelarte algo que los medios nunca han publicado."
   Esto ancla al espectador a la segunda mitad del vídeo.

3. DIRECCIÓN AL ESPECTADOR (mínimo 4 veces en el guión):
   Usa "Tú que estás escuchando esto ahora mismo...", "Piensa en esto por un momento...", "¿Te imaginas estar en su lugar?"
   Crea conexión parasocial y saca al espectador del modo pasivo.

4. ESPECIFICIDAD EXTREMA — el arma más poderosa:
   No "hace mucho tiempo" → "el 23 de febrero de 1981, a las 18 horas y 23 minutos"
   No "mucho dinero" → "exactamente 847.000 dólares"
   No "un hombre" → "Victor Lustig, de 43 años, con un sombrero negro y un maletín de cuero marrón"
   Los detalles concretos crean credibilidad instantánea aunque el espectador no pueda verificarlos.

5. RITMO DINÁMICO — alterna siempre:
   Frase corta de impacto. Luego una frase larga que desarrolla con detalle sensorial. Pausa dramática implícita.
   Nunca más de 3 frases del mismo ritmo seguidas.

6. CLIFFHANGERS OBLIGATORIOS — uno cada 4-5 minutos:
   Cada bloque termina con algo que contradice lo que el espectador acaba de asumir.
   Ejemplos potentes: "Y entonces descubrieron algo que no debían haber visto." / "Lo que nadie sabe es que eso fue solo el principio."

7. MUESTRA, NO EXPLIQUES:
   Recrea escenas como si fuera una película. Pon diálogos inventados pero verosímiles.
   Describe con los 5 sentidos: olores, temperaturas, texturas, sonidos.

8. REGLAS TÉCNICAS:
   - Narración oral pura: cero símbolos, asteriscos, markdown, listas
   - Sin afirmaciones falsas verificables sobre personas vivas
   - Sin contenido inapropiado
   - Devuelve SOLO el texto narrado, listo para locutar"""


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
HOOK (~450 palabras — EL LOOP PROHIBIDO — los primeros 3 minutos son vida o muerte)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASO 1 — EL GOLPE INICIAL (primeras 3 frases):
Empieza directamente con el momento más impactante de toda la historia. La escena climática. El dato que nadie conoce. La fecha exacta del desastre. Sin presentación, sin contexto previo. Directo al impacto máximo.

PASO 2 — EL LOOP (frases 4-6):
Después de ese golpe inicial, di algo como:
"Pero para entender cómo llegamos a este punto, tengo que llevarte [X años/meses] atrás."
"Lo que estás a punto de escuchar cambió todo. Y lo más perturbador es que empezó con algo completamente ordinario."
Esto crea el bucle: el espectador SABE que hay algo enorme al final y tiene que quedarse para entender el camino.

PASO 3 — LA PROMESA ESPECÍFICA:
"En los próximos 30 minutos vas a descubrir [algo concreto y específico que van a aprender]. Y antes de que acabe este vídeo, voy a revelarte algo que [los medios / los documentos oficiales / la versión oficial] nunca han contado."

PASO 4 — EL ANZUELO EMOCIONAL:
Termina el hook con una pregunta directa al espectador: "¿Tú qué habrías hecho en su lugar?" o una imagen mental tan vívida e inquietante que sea físicamente imposible cerrar el vídeo.

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
