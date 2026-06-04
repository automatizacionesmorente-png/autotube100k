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

# HÍBRIDO ÓPTIMO (calidad máxima al mínimo coste):
#   HOOK → Opus (decide la retención, es corto = barato)
#   CUERPO → Sonnet (5x más barato, prosa casi idéntica para narrar largo)
# Resultado: calidad ~Opus a ~0.46-0.50€/vídeo en vez de ~0.85€.
MODEL = "claude-sonnet-4-6"   # cuerpo del guion (el grueso de palabras)
MODEL_IN_PRICE  = 3
MODEL_OUT_PRICE = 15

HOOK_MODEL = "claude-opus-4-8"   # solo el hook — máxima calidad donde más importa
HOOK_IN_PRICE  = 15
HOOK_OUT_PRICE = 75
EUR_RATE = 0.92

TARGET_WORDS = 5200   # ~34-36 min a 145 pal/min — SIEMPRE +30 min (con margen)
MIN_WORDS    = 4800   # 4800 pal ≈ 33 min — nunca por debajo de 30 min
MAX_WORDS    = 5800


# ═══════════════════════════════════════════════════════════════════════════
# HOOK — generación DEDICADA (los 30 primeros segundos deciden todo)
# ═══════════════════════════════════════════════════════════════════════════

HOOK_SYSTEM_PROMPT = """Eres el mejor escritor de HOOKS de YouTube del mundo en español. Tu única obsesión: los primeros 30-40 segundos, el instante exacto en que el cerebro del espectador decide si se queda o hace scroll. Tus hooks retienen el 90% en los primeros 30 segundos (nivel MrBeast). Cada hook que escribes es de nivel viral mundial.

LAS 8 LEYES DEL HOOK PERFECTO (violar una es fracasar):

1. LA PRIMERA FRASE ES UN MARTILLAZO (regla de oro).
   Corta: idealmente menos de 10 palabras. Visceral. Un pattern interrupt que rompe el patrón mental del espectador en los primeros 5 segundos (= +23% de retención).
   Prohibido empezar con contexto, fecha, presentación o "hoy te voy a hablar de...".
   Si tu primera frase podría aparecer en cualquier otro vídeo, BÓRRALA y escribe otra.

2. PREMISA CLARA EN 3-8 SEGUNDOS.
   Tras el martillazo, el espectador debe entender de QUÉ va esto y por qué le importa. Sin rodeos.

3. HÁBLALE A ÉL, NO DEL TEMA.
   Que sienta que le hablas a ÉL, que sabes algo de su vida, que esto es para él HOY. Usa "tú", "te", "tu". Mínimo 2 interpelaciones directas.

4. APILA BUCLES ABIERTOS (técnica de máxima retención).
   No abras solo un bucle: abre 2 o 3 preguntas/promesas sin resolver que se acumulan. Cada bucle sin cerrar es una razón más para no irse. El cerebro NECESITA el cierre.

5. SECRETO / CONOCIMIENTO PROHIBIDO.
   Haz sentir que va a acceder a algo que casi nadie sabe: "lo que no te contaron", "lo que el 99% nunca entiende", "lo que ocultaron". Que quedarse le dé una ventaja sobre los demás.

6. ESPECIFICIDAD QUE DA CREDIBILIDAD.
   Un dato, cifra o detalle concreto y real golpea diez veces más que una generalidad. Lo concreto se siente verdadero.

7. RITMO DE PELÍCULA.
   Frases cortas. Pausa. Una frase larga que sumerge. Otra corta que golpea. Nunca monótono. Que se lea como un tráiler.

8. CERO RELLENO + FINAL EN EL FILO.
   Cada palabra gana su sitio. Termina en el punto de máxima tensión, justo cuando es imposible cerrar el vídeo (un anzuelo suspendido, sin resolver).

REGLAS TÉCNICAS:
- Solo texto narrado (sin acotaciones, símbolos ni markdown).
- 170-220 palabras (unos 75-90 segundos de narración).
- No inventes datos verificables falsos (nombres, cifras, fechas, resultados). Si no es real, no lo afirmes.
- Autoexigencia: antes de entregar, relee tu primera frase. Si no es un 10/10 que detiene el scroll, reescríbela. Devuelve solo el hook final."""


# Estrategia de apertura según el género (lo que cambia el hook de raíz)
HOOK_STRATEGY = {
    "motivacional": """ESTRATEGIA (MOTIVACIÓN/SUPERACIÓN):
Habla directo al alma del espectador y a su momento de hoy. Hazle sentir VISTO.
Apertura tipo: señálalo ("Si este vídeo ha llegado a ti hoy, no es casualidad..."), nombra su dolor o su lucha silenciosa, y promete una transformación concreta. Crea sensación de destino y urgencia emocional. Tono: cercano, con peso, pausas. Es un mensaje personal, no una clase.""",
    "misterio": """ESTRATEGIA TRUE CRIME / MISTERIO — ESTRUCTURA EXACTA EN 5 ACTOS:

ACTO 1 — LA BOMBA (primeras 2-3 frases, ~15 segundos):
Lanza el hecho más perturbador de toda la historia. Sin contexto, sin fecha previa, sin presentación.
Habla en PRESENTE HISTÓRICO (más inmediato y cinematográfico).
Ejemplo de nivel: "Tres niñas caminan hacia casa. Es una tarde de noviembre normal. No van a llegar."
Prohibido: "Hoy os voy a hablar de...", "En el año...", "Este vídeo trata sobre..."

ACTO 2 — EL AMPLIFICADOR (2-3 frases, ~20 segundos):
Añade un detalle sensorial o un dato que hace LA BOMBA todavía más perturbador.
Usa frío, oscuridad, silencio, olores, temperatura. El espectador debe sentir algo físico.
Ejemplo: "Hacía frío. Uno de esos fríos que se meten en los huesos. Los agentes que llegaron primero dijeron que ese olor nunca lo olvidarían."

ACTO 3 — EL LOOP (3-4 frases, ~25 segundos):
Crea el bucle narrativo: menciona que para entender el desenlace hay que ir atrás.
FÓRMULA EXACTA: "Pero para entender lo que ocurrió [aquí/ese día/en ese momento], tengo que llevarte [X tiempo] atrás. A un [día/momento] que parecía completamente normal."
Este bucle hace IMPOSIBLE cerrar el vídeo.

ACTO 4 — LA PROMESA PROHIBIDA (2-3 frases, ~20 segundos):
Garantiza al espectador que va a descubrir algo que los medios ocultaron o que nadie ha contado.
FÓRMULA: "En los próximos [X] minutos vas a descubrir [algo específico y concreto]. Lo que [los medios / la versión oficial / los documentos oficiales] nunca quisieron que supieras."
Sé específico. "Lo que nadie sabe" es genérico. Di exactamente qué van a descubrir.

ACTO 5 — EL ANZUELO FINAL (1-2 frases, ~10 segundos):
Una frase tan perturbadora que cerrar el vídeo resulta psicológicamente imposible.
Que quede suspendida en el aire. Sin resolver. Que duela.
Ejemplo de nivel: "Y lo más perturbador de todo es que hay alguien que sabe exactamente lo que pasó. Y lleva treinta años caminando libre."

REGLAS ADICIONALES:
- Frases cortas. Punto. Pausa implícita. Otra frase corta. Funciona como una película.
- Mínimo 2 veces dirigirse directamente al espectador: "Tú que estás escuchando esto..."
- Tono: grave, pausado, como alguien que te va a contar un secreto que cambia todo.""",

    "drama": """ESTRATEGIA (DRAMA/HISTORIA HUMANA) — ESTRUCTURA EN 5 ACTOS:

ACTO 1 — EL INSTANTE CUMBRE: Abre directamente en el segundo exacto de máximo impacto emocional. Una pérdida, una traición, la decisión imposible. Con UN protagonista concreto (nombre real si lo sabes, o "una mujer", "un hombre de 43 años"). Presente histórico.

ACTO 2 — EL DETALLE HUMANO: Un detalle pequeño y concreto que hace al protagonista real: qué llevaba puesto, qué pensaba en ese momento, qué había desayunado esa mañana. Lo mundano antes del desastre es devastador.

ACTO 3 — EL LOOP: "Para entender cómo [el protagonista] llegó a ese momento, tengo que llevarte [X tiempo] atrás."

ACTO 4 — LA PROMESA EMOCIONAL: Qué va a sentir el espectador al final. No solo qué va a saber, sino cómo va a sentirse diferente después de este vídeo.

ACTO 5 — EL ANZUELO: Una pregunta al espectador que le implica personalmente. "¿Tú qué habrías hecho en su lugar?"

Tono: íntimo, emocional, pausado. Como si le hablaras al oído a una sola persona.""",
    "documental": """ESTRATEGIA (DOCUMENTAL/DIVULGACIÓN):
Abre con el dato o hecho más asombroso y contraintuitivo del tema, uno que rompa lo que el espectador creía saber. "Lo que estás a punto de descubrir contradice todo lo que te contaron." Aporta credibilidad inmediata y promete revelar lo que casi nadie sabe.""",
    "humor": """ESTRATEGIA (HUMOR/CERCANO):
Abre con una observación absurda, relatable o exagerada que arranque una sonrisa o un "es verdad". Complicidad inmediata. Luego promete una historia tan increíble que parece mentira (pero es real).""",
    "neutro": """ESTRATEGIA (GENERAL):
Abre con una afirmación audaz o una pregunta que despierte curiosidad inmediata sobre el tema, hablándole al espectador. Promete algo concreto y valioso que va a obtener por quedarse.""",
}


HOOK_ANGLES = [
    "ÁNGULO DE APERTURA: abre con el HECHO o DATO más impactante, perturbador o inesperado de toda la historia. Shock puro en la primera frase. Que el espectador piense '¿qué acaba de decir?'.",
    "ÁNGULO DE APERTURA: abre INTERPELANDO directamente al espectador, tocando una emoción, un miedo o una creencia suya. Que sienta que le hablas a ÉL, que sabes algo de él. Ej: 'Lo diste por muerto. Reconócelo.'",
    "ÁNGULO DE APERTURA: abre con una ESCENA cinematográfica vívida en PRESENTE, como el primer plano de una película. Detalle sensorial inmediato (frío, silencio, un gesto). Que el espectador VEA la escena.",
]


# CONCEPTO de los hooks de mayor retención (inspirado en la competencia analizada:
# vídeos tipo "lo que nadie te cuenta / 4 cosas que nunca debes...").
HOOK_CONCEPT = """CONCEPTO CLAVE — el patrón de los hooks que más retienen (úsalo SIEMPRE, integrado con naturalidad):
Haz sentir al espectador que está a punto de acceder a algo que CASI NADIE sabe: un secreto, una verdad incómoda, "lo que no te han contado", "lo que ocultaron", "lo que el 99% nunca entiende".
- Lanza una promesa CONCRETA y específica de lo que va a descubrir (mejor si es contraintuitivo o suena casi prohibido).
- Transmite que quedarse le da una VENTAJA o un conocimiento que los demás no tienen.
- Interpélalo directamente: que sienta que le hablas a ÉL, justo hoy.
Patrones de ejemplo (adáptalos al tema, NO los copies literal):
"Hay algo sobre esto que el 99% de la gente nunca llega a entender."
"Durante años nos contaron una versión. La verdad es muy distinta."
"Lo que voy a contarte cambió por completo cómo veo esto, y casi nadie lo sabe."
"""


def generate_hook(client, niche: str, title: str, tone: str, context_block: str = "") -> str:
    """
    Hook de máxima calidad en UNA sola llamada Opus (sin método de 3 — más barato,
    ~0.06€/vídeo menos). Integra el concepto de los hooks de mayor retención de la
    competencia + la estrategia por género. Devuelve (texto, usage).
    """
    strategy = HOOK_STRATEGY.get(tone, HOOK_STRATEGY["neutro"])
    user = f"""TEMA DEL VÍDEO: {title}
NICHO: {niche}
{strategy}{context_block}

{HOOK_CONCEPT}

Escribe SOLO el HOOK (los primeros 75-90 segundos, 170-220 palabras).
- La PRIMERA frase es un PATTERN INTERRUPT que detiene el scroll en los primeros 5 segundos (un golpe imposible de ignorar). +23% de retención si está bien.
- La premisa central queda clara en los primeros 3-8 segundos.
- Háblale directamente al espectador (a TI). Suelta algo que se sienta como un secreto o conocimiento prohibido.
- Abre un bucle imposible de cerrar y acaba en máxima tensión.
Aplica sin piedad las 6 leyes del hook perfecto. Cero relleno. Solo texto narrado, sin comillas ni acotaciones."""
    m = client.messages.create(
        model=HOOK_MODEL, max_tokens=600,
        system=HOOK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    return m.content[0].text.strip(), m.usage

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

4. RIGOR FACTUAL — REGLA INVIOLABLE (más importante que cualquier otra):
   PROHIBIDO inventar datos verificables: nombres propios, fechas, cifras, estadísticas,
   resultados, marcadores, citas textuales o declaraciones que no sean REALES y conocidos.
   - Si NO conoces un dato concreto con certeza, NO lo inventes. Habla en términos generales
     y ciertos, o enmárcalo como expectativa: "se espera que...", "según las previsiones...",
     "históricamente...", "todo apunta a que...".
   - Sobre eventos futuros o en curso: NUNCA des resultados o hechos que aún no han ocurrido
     como si ya hubieran pasado. Distingue siempre lo confirmado de lo previsto.
   - Las citas entre comillas solo si son declaraciones reales y documentadas. Si no, parafrasea
     sin comillas o no las uses.
   La credibilidad real viene de los hechos ciertos, no de inventar detalles.

5. ESPECIFICIDAD CON DATOS REALES:
   Sé concreto SOLO con información verídica que conozcas (fechas, lugares, cifras reales).
   Cuando no tengas un dato exacto, gana fuerza con descripción sensorial y atmosférica
   (que NO es una afirmación factual): el ambiente, la tensión, las emociones, el contexto.
   Nunca rellenes huecos con cifras o nombres inventados.

6. RITMO DINÁMICO — alterna siempre:
   Frase corta de impacto. Luego una frase larga que desarrolla con detalle. Pausa dramática implícita.
   Nunca más de 3 frases del mismo ritmo seguidas.

7. CLIFFHANGERS OBLIGATORIOS — uno cada 4-5 minutos:
   Cada bloque termina con algo que mantiene la tensión y la curiosidad.
   Ejemplos: "Pero esto era solo el principio." / "Y lo que vino después lo cambió todo."

8. MUESTRA, NO EXPLIQUES:
   Recrea el ambiente y el contexto de forma vívida y cinematográfica, con detalle sensorial
   (sin inventar hechos). Haz sentir la escena sin fabricar datos ni diálogos falsos.

9. REGLAS TÉCNICAS:
   - Narración oral pura: cero símbolos, asteriscos, markdown, listas
   - CERO afirmaciones falsas o inventadas, especialmente sobre personas y eventos reales
   - Sin contenido inapropiado
   - Devuelve SOLO el texto narrado, listo para locutar"""


def generate_script(job_id: str, niche: str, title: str, tone: str, context: str = None) -> str:
    add_step(job_id, "script", "running", "Generando guión con Claude Sonnet 4.6…")
    tone_desc = TONES.get(tone, TONES["neutro"])
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    context_block = ""
    if context and context.strip():
        context_block = f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATOS VERIFICADOS Y REALES (USO OBLIGATORIO)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Estos son hechos REALES y actuales verificados. Úsalos como base factual del vídeo.
NO inventes datos que contradigan o vayan más allá de esto. Para cualquier detalle
concreto (nombres, fechas, cifras, resultados) que no esté aquí, NO lo inventes:
habla en términos generales ciertos o como expectativa ("se espera", "según las previsiones").

{context.strip()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    # ════════ ETAPA 1: HOOK DEDICADO (los 30s que deciden todo) ════════
    add_step(job_id, "script", "running", "✍️ Escribiendo el HOOK (los 30 segundos que deciden todo)…")
    hook_text, hook_usage = generate_hook(client, niche, title, tone, context_block)
    hook_cost = (hook_usage.input_tokens * HOOK_IN_PRICE +
                 hook_usage.output_tokens * HOOK_OUT_PRICE) / 1_000_000 * EUR_RATE
    add_cost_event(job_id, "claude_opus_hook", hook_usage.output_tokens,
                   HOOK_OUT_PRICE / 1_000_000, hook_cost)
    hook_words = len(hook_text.split())

    # ════════ ETAPA 2: CUERPO que continúa el hook sin repetirlo ════════
    add_step(job_id, "script", "running",
             f"Hook listo ({hook_words} pal). Generando el cuerpo con Claude Sonnet 4.6…")

    user_prompt = f"""NICHO: {niche}
TÍTULO: {title}
TONO: {tone_desc}{context_block}

El HOOK del vídeo YA está escrito (no lo repitas, no lo reescribas). Es este:
─────────────────────────────────────
{hook_text}
─────────────────────────────────────

Tu tarea: escribir el CUERPO del vídeo que CONTINÚA de forma fluida y natural justo después de ese hook, como si fuera la misma voz sin pausa. Mantén la promesa y el bucle que abrió el hook y ciérralos a lo largo del vídeo (y del todo al final).

Extensión CRÍTICA del cuerpo: entre {MIN_WORDS - hook_words} y {MAX_WORDS - hook_words} palabras (el hook ya aporta {hook_words}).
La primera frase del cuerpo debe enlazar de forma natural con el final del hook (sin volver a saludar ni reintroducir el tema).

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

    body = msg.content[0].text.strip()
    script = hook_text + "\n\n" + body   # hook dedicado + cuerpo
    word_count = len(script.split())

    body_cost = (msg.usage.input_tokens * MODEL_IN_PRICE +
                 msg.usage.output_tokens * MODEL_OUT_PRICE) / 1_000_000 * EUR_RATE
    add_cost_event(job_id, "claude_sonnet_body", msg.usage.output_tokens,
                   MODEL_OUT_PRICE / 1_000_000, body_cost)
    cost_eur = hook_cost + body_cost

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
