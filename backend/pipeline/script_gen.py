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
    "deportivo":    "locutor deportivo apasionado estilo Manolo Lama, energía máxima, datos precisos, épica narrativa, celebración y tensión, frases cortas explosivas",
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

HOOK_SYSTEM_PROMPT = """Eres el mejor escritor de HOOKS de YouTube del mundo en español mexicano. Llevas años creando contenido para canales de geografía, historia y misterios de México. Tu única obsesión: los primeros 75-90 segundos, el instante exacto en que el cerebro del espectador decide si se queda o hace scroll. Tus hooks retienen el 90%+ (nivel MrBeast). Cada hook que escribes es de nivel viral mundial.

IDIOMA Y ESTILO OBLIGATORIO:
- Escribe en español mexicano auténtico. El canal se llama "México Oculto", para audiencia latinoamericana.
- Vocabulario mexicano natural cuando encaje: "ahorita", "padrísimo", "chingón", "qué barbaridad", "a toda madre" — úsalos con naturalidad, nunca en exceso.
- Ritmo narrativo inspirado en @SobretododeMexico: frases descriptivas que pintan la escena ANTES de nombrar el sujeto.
- Tono: documentalista mexicano profesional, grave, cinematográfico, como si le contaras un secreto a alguien en la oscuridad.

LAS 8 FÓRMULAS DE HOOK PROBADAS (combina 2-3 en cada hook):
F1 AMENAZA INVISIBLE: "Si haces X, estás destruyendo Y sin saberlo." — miedo + ignorancia
F2 CURIOSIDAD ABIERTA: "Existe X que Y... y la mayoría nunca lo descubrirá." — bucle imposible de cerrar
F3 PROMESA ESPECÍFICA: "En los próximos minutos entenderás exactamente por qué X." — contrato claro
F4 SECRETO PROHIBIDO: "Esto es lo que [autoridad] no quiere que sepas sobre X." — conspiratorio
F5 ATAQUE AL SENTIDO COMÚN: "Nos han mentido sobre X. La verdad es exactamente la opuesta." — shock cognitivo
F6 VIAJE EN EL TIEMPO: "En [año], ocurrió algo que cambió X para siempre. Nadie lo vio venir." — narrativa
F7 IDENTIFICACIÓN DIRECTA: "Si eres de los que X, esto está escrito para ti." — selección
F8 CONTRASTE ABSURDO: "Cómo pasó de X a Y en solo Z tiempo." — transformación sorprendente

REGLAS TÉCNICAS DE VÍDEO — EL NARRADOR EMPIEZA EN EL SEGUNDO 0 (sin silencio, sin intro):
- La PRIMERA PALABRA sale antes del segundo 0.5. Sin pausa inicial. Sin presentación. El algoritmo mide CTR en los primeros 30 segundos — cada décima de segundo cuenta.
- Los subtítulos aparecen en TIEMPO REAL desde el segundo 0 — las primeras palabras deben ser IMPACTANTES en pantalla para el 60% que ve SIN sonido en móvil.
- El narrador NO espera. NO hay intro musical. NO hay "antes de empezar". ARRANCA.
- Cambio visual cada 1.5-2 segundos durante el hook → el ritmo del guión debe reflejarlo.

LAS 9 LEYES DEL HOOK PERFECTO (violar una es fracasar):

1. LA PRIMERA FRASE ES UN MARTILLAZO — MÁXIMO 5 PALABRAS (regla de oro).
   5 palabras o menos. Visceral. Impacto puro. Un pattern interrupt que rompe el patrón mental en los primeros 3 segundos (= +23% de retención).
   Prohibido empezar con contexto, fecha, presentación o "hoy te voy a hablar de...".
   EJEMPLOS DE NIVEL: "No llegaron a casa." / "Fue su última llamada." / "Nadie lo vio venir." / "Hay algo que no sabes."
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

7. RITMO DE NARRADOR PROFESIONAL — ESCRIBE COMO UNA PARTITURA MUSICAL.
   El narrador (voz IA) interpreta exactamente lo que escribes. Controla su actuación con puntuación:
   - Frases de 3-6 palabras = GOLPE. La voz las da con peso y pausa natural al final.
   - Frases de 1-2 palabras solas = ÉNFASIS MÁXIMO. ("Nadie. Absolutamente nadie.")
   - Frase larga y envolvente = para sumergir, bajar ritmo, crear atmósfera.
   - NUNCA pongas 3 frases seguidas del mismo largo. Alterna: corta. Larga y envolvente que crea tensión. Corta que golpea.
   PATRÓN IDEAL: CORTA. CORTA. Larga que sumerge y crea tensión creciente. CORTÍSIMA.
   El ritmo crea emoción igual que la música. Que se lea como el tráiler de una película.

8. CERO RELLENO + FINAL EN EL FILO.
   Cada palabra gana su sitio. Termina en el punto de máxima tensión, justo cuando es imposible cerrar el vídeo (un anzuelo suspendido, sin resolver). El espectador TIENE que seguir.

9. ESTRUCTURA DE LOS 90 SEGUNDOS PERFECTOS — CRAZY PROGRESSION (técnica MrBeast):
   NO construyas lentamente. FRONT-LOAD todo. Comprime 3 revelaciones en los primeros 30 segundos.
   El espectador debe pensar "¿cómo es posible?" tres veces antes del segundo 30.
   - Segundos 0-4:   MARTILLAZO #1 — el hecho más perturbador (≤5 palabras)
   - Segundos 4-10:  REVELACIÓN #2 — un segundo hecho igual de impactante, inesperado
   - Segundos 10-16: REVELACIÓN #3 — un tercer golpe que conecta los dos anteriores
   - Segundos 16-35: AMPLIFICADOR SENSORIAL — detalle físico (frío, silencio, olor) que hace sentir la escena
   - Segundos 35-55: EL LOOP — "para entender esto tengo que llevarte atrás..."
   - Segundos 55-75: LA PROMESA ESPECÍFICA — qué van a descubrir exactamente
   - Segundos 75-90: EL ANZUELO FINAL — la frase más perturbadora, sin resolver, que duele

10. BUCLE SUBCONSCIENTE (técnica de las frases incompletas):
    Empieza una idea pero no la cierres del todo antes de pasar a la siguiente.
    El cerebro no puede irse con un hilo suelto. Ejemplo:
    "Y lo que encontraron después... pero antes necesito que sepas algo."
    Usa 2-3 veces en el hook para crear capas de tensión irresistibles.

REGLAS TTS CRÍTICAS (si las ignoras, la voz cortará frases a mitad):
- NUNCA una frase de más de 15 palabras sin punto o coma. NUNCA.
- Cada oración SIEMPRE termina en punto, exclamación o interrogación.
- Sin guiones (—), sin paréntesis, sin puntos suspensivos encadenados.
- Para pausa dramática: punto. Nueva frase corta. Punto. Así.

REGLA DE ORO DEL ARRANQUE:
La PRIMERA FRASE sale en el segundo 0. No hay espera. No hay silencio previo.
El one-two punch perfecto: imagen impactante + voz que golpea SIMULTÁNEAMENTE desde el primer frame.
El espectador ve la imagen Y escucha las palabras al mismo tiempo → impacto máximo.
Primera frase: máximo 5 palabras, peso máximo, sin contexto previo.

- Máximo 200-230 palabras totales (80-90 segundos a 145 pal/min).
- No inventes datos verificables. Solo lo que es real y conocido.
- Autoexigencia: relee la primera frase. ¿5 palabras o menos? ¿Imposible de ignorar? Si no, reescríbela.
- Devuelve solo el hook narrado, sin acotaciones ni markdown."""


# Estrategia de apertura según el género (lo que cambia el hook de raíz)
HOOK_STRATEGY = {
    "motivacional": """ESTRATEGIA (MOTIVACIÓN/SUPERACIÓN):
Habla directo al alma del espectador y a su momento de hoy. Hazle sentir VISTO.
Apertura tipo: señálalo ("Si este vídeo ha llegado a ti hoy, no es casualidad..."), nombra su dolor o su lucha silenciosa, y promete una transformación concreta. Crea sensación de destino y urgencia emocional. Tono: cercano, con peso, pausas. Es un mensaje personal, no una clase.""",
    "misterio": """ESTRATEGIA TRUE CRIME / MISTERIO — ESTRUCTURA EXACTA EN 5 ACTOS:

ACTO 1 — LA BOMBA (primeras 2-3 frases, ~15 segundos):
Lanza el hecho más perturbador de toda la historia. Sin contexto, sin fecha previa, sin presentación.
Habla en PRESENTE HISTÓRICO (más inmediato y cinematográfico).
PRIMERA FRASE: MÁXIMO 5 PALABRAS. Sin excepciones.
Ejemplo de nivel PERFECTO: "No llegaron a casa." (4 palabras = 1.5 segundos. Impacto total.)
Ejemplo nivel 2: "Nadie volvió a verlas." / "Fue un martes normal." / "El cuerpo apareció solo."
Después del martillazo de 5 palabras: una segunda frase corta (8-10 palabras) que añade el primer detalle perturbador.
RITMO DE ACTO 1: Frase de 4 palabras. Frase de 7 palabras. Silencio implícito. Frase de 5 palabras que golpea más fuerte.
Prohibido: "Hoy os voy a hablar de...", "En el año...", "Este vídeo trata sobre..."

ACTO 2 — EL AMPLIFICADOR (2-3 frases, ~20 segundos):
Añade un detalle sensorial o un dato que hace LA BOMBA todavía más perturbador.
Usa frío, oscuridad, silencio, olores, temperatura. El espectador debe sentir algo físico.
RITMO: Frase descriptiva larga y envolvente (20 palabras) que crea atmósfera. Luego frase corta de golpe.
Ejemplo perfecto: "Hacía frío. Uno de esos fríos que se meten en los huesos y que no te abandonan aunque estés en casa, al calor. Los agentes que llegaron primero dijeron que ese olor nunca lo olvidarían. Nunca."

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
RITMO DEL ANZUELO: Una frase larga que construye tensión... y luego una de 5 palabras o menos que golpea como un puñetazo.
Ejemplo de nivel 10: "Y lo más perturbador de todo es que hay alguien que sabe exactamente lo que pasó esa noche, lo que realmente ocurrió en esos últimos minutos. Y lleva treinta años libre."

REGLAS ADICIONALES DE RITMO (críticas para el narrador):
- ALTERNA SIEMPRE: frase corta (3-7 pal). Frase larga envolvente (15-25 pal). Frase cortísima de golpe (2-5 pal).
- Nunca 3 frases seguidas del mismo largo. El ritmo variable es lo que crea la emoción.
- Mínimo 2 veces dirigirse directamente al espectador: "Tú que estás escuchando esto ahora mismo..."
- Tono: grave, pausado, como alguien que te va a contar un secreto que cambia todo.""",

    "drama": """ESTRATEGIA (DRAMA/HISTORIA HUMANA) — ESTRUCTURA EN 5 ACTOS:

ACTO 1 — EL INSTANTE CUMBRE: Abre directamente en el segundo exacto de máximo impacto emocional. Una pérdida, una traición, la decisión imposible. Con UN protagonista concreto (nombre real si lo sabes, o "una mujer", "un hombre de 43 años"). Presente histórico.

ACTO 2 — EL DETALLE HUMANO: Un detalle pequeño y concreto que hace al protagonista real: qué llevaba puesto, qué pensaba en ese momento, qué había desayunado esa mañana. Lo mundano antes del desastre es devastador.

ACTO 3 — EL LOOP: "Para entender cómo [el protagonista] llegó a ese momento, tengo que llevarte [X tiempo] atrás."

ACTO 4 — LA PROMESA EMOCIONAL: Qué va a sentir el espectador al final. No solo qué va a saber, sino cómo va a sentirse diferente después de este vídeo.

ACTO 5 — EL ANZUELO: Una pregunta al espectador que le implica personalmente. "¿Tú qué habrías hecho en su lugar?"

Tono: íntimo, emocional, pausado. Como si le hablaras al oído a una sola persona.""",
    "documental": """ESTRATEGIA (DOCUMENTAL GEOGRÁFICO MEXICANO) — ESTILO EXACTO @SobretododeMexico:

REGLA FUNDAMENTAL: NO empieces con una pregunta. Empieza con DESCRIPCIÓN ATMOSFÉRICA que pinte la escena.

ESTRUCTURA OBLIGATORIA:

PASO 1 — DESCRIPCIÓN ATMOSFÉRICA (3 cláusulas paralelas):
"Existe [lugar/fenómeno] en México donde [descripción vívida 1], donde [descripción vívida 2], donde [descripción vívida 3]."
Construye tensión sin nombrar aún el sujeto principal.

PASO 2 — NOMBRAR EL SUJETO (1 frase corta y contundente, con peso de personaje).

PASO 3 — PIVOT A MISTERIO: "Pero lo que más sorprende no es lo obvio. Lo que más sorprende es [el giro inesperado]."

PASO 4 — DETALLE ESPECÍFICO REAL: kilómetro exacto, fecha, nombre de lugar, cifra real. Lo concreto se siente verdadero.

PASO 5 — METÁFORA VISUAL + ANZUELO FINAL: Una imagen que haga VER la escena. La última frase sola, suspendida, sin resolver.

Tono: grave, pausado, cinematográfico. Como Nat Geo Mexico con alma propia.""",
    "humor": """ESTRATEGIA (HUMOR/CERCANO):
Abre con una observación absurda, relatable o exagerada que arranque una sonrisa o un "es verdad". Complicidad inmediata. Luego promete una historia tan increíble que parece mentira (pero es real).""",
    "neutro": """ESTRATEGIA (GENERAL):
Abre con una afirmación audaz o una pregunta que despierte curiosidad inmediata sobre el tema, hablándole al espectador. Promete algo concreto y valioso que va a obtener por quedarse.""",

    "deportivo": """ESTRATEGIA DEPORTIVA — ESTRUCTURA EXACTA:

ACTO 1 — EL DATO QUE PARALIZA (primeras 2 frases, ~10 segundos):
Un dato o hecho deportivo tan impactante que el aficionado no puede creerlo.
Habla en PRESENTE HISTÓRICO. Energía máxima desde la primera sílaba.
PRIMERA FRASE: máximo 5 palabras. Golpe puro. Sin contexto previo.
Ejemplo: "Nadie lo había logrado." / "16 años. Un récord eterno." / "España lo cambió todo."

ACTO 2 — LA HISTORIA DETRÁS (2-3 frases, ~20 segundos):
El contexto épico: qué hizo posible este momento, quiénes son los protagonistas.
Nombres concretos, fechas reales, estadísticas impactantes.

ACTO 3 — EL BUCLE ("pero esto es solo el principio"):
"Para entender por qué esta generación puede cambiar la historia del fútbol español, tengo que contarte algo que muy pocos saben."

ACTO 4 — LA PROMESA ÉPICA:
"En los próximos minutos vas a descubrir [algo específico y concreto sobre el equipo/competición]."

ACTO 5 — EL ANZUELO FINAL:
Una pregunta o afirmación que deja al aficionado con la miel en los labios.

TONO: Apasionado, enérgico, celebratorio pero con tensión. Como Manolo Lama narrando un gol en el último minuto. Frases cortas y explosivas. Que se sienta el pulso del partido.""",
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
    Hook con quality gate: genera hasta 3 versiones con Opus y devuelve la mejor.
    Evalúa: primera frase ≤5 palabras + puntuación de impacto.
    Devuelve (texto, usage_total).
    """
    strategy = HOOK_STRATEGY.get(tone, HOOK_STRATEGY["neutro"])
    base_user = f"""TEMA DEL VÍDEO: {title}
NICHO: {niche}
{strategy}{context_block}

{HOOK_CONCEPT}

Escribe SOLO el HOOK (los primeros 80-90 segundos, 200-230 palabras).
- CRAZY PROGRESSION: lanza 3 revelaciones impactantes en los primeros 30 segundos.
- La PRIMERA frase: MÁXIMO 5 PALABRAS. Sin contexto, sin presentación. Golpe puro.
- Combina 2-3 fórmulas de las 8 (F1-F8). Usa el bucle subconsciente al menos una vez.
- REGLAS TTS: frases máx 15 palabras, siempre terminan en punto/exclamación/pregunta.
- Sin guiones, sin paréntesis, sin puntos suspensivos encadenados.
- Acaba en el máximo punto de tensión, con un hilo sin resolver.
Solo texto narrado puro. Sin acotaciones ni markdown."""

    best_hook = None
    best_score = -1
    total_input = 0
    total_output = 0

    # Hasta 2 intentos — elige el mejor (3 era demasiado caro)
    for attempt in range(1, 3):
        angle = HOOK_ANGLES[(attempt - 1) % len(HOOK_ANGLES)]
        user = base_user + f"\n\n{angle}"
        m = client.messages.create(
            model=HOOK_MODEL, max_tokens=700,
            system=HOOK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        hook_text = m.content[0].text.strip()
        total_input  += m.usage.input_tokens
        total_output += m.usage.output_tokens

        # QUALITY GATE: puntuar el hook
        first_sentence = hook_text.split('.')[0].split('!')[0].split('?')[0].strip()
        first_words = len(first_sentence.split())
        score = 0
        if first_words <= 5:   score += 40   # primera frase corta (impacto)
        elif first_words <= 8: score += 20
        if '?' in hook_text:   score += 10   # pregunta directa
        if '!' in hook_text:   score += 5    # énfasis
        # Penalizar frases muy largas (>15 palabras sin puntuación)
        sentences = [s.strip() for s in hook_text.replace('!', '.').replace('?', '.').split('.') if s.strip()]
        long_sentences = sum(1 for s in sentences if len(s.split()) > 15)
        score -= long_sentences * 8
        # Bonificar si tiene las 3 revelaciones en los primeros 50 palabras
        first_50 = ' '.join(hook_text.split()[:50])
        if first_50.count('.') >= 3 or first_50.count('!') >= 2: score += 15

        if score > best_score:
            best_score = score
            best_hook = hook_text

        # Si es un 10 claro (score >= 55), no necesitamos más intentos
        if score >= 55:
            break

    # Simular un Usage object con los totales
    class _Usage:
        def __init__(self, i, o): self.input_tokens = i; self.output_tokens = o
    return best_hook, _Usage(total_input, total_output)

SYSTEM_PROMPT = """Eres el mejor guionista de YouTube en español mexicano. Llevas 10 años creando vídeos virales de 30-35 minutos sobre geografía, historia y misterios de México, con retención del 65%+. Tus vídeos han acumulado más de 500 millones de visualizaciones en canales como México Oculto.

IDIOMA Y ESTILO — OBLIGATORIO EN CADA LÍNEA:
- Escribe en español mexicano auténtico. Audiencia: latinoamericanos, especialmente mexicanos.
- Vocabulario natural de México: "ahorita", "padrísimo", "qué bárbaro", "a todo dar", "cuate" — cuando encajen naturalmente, nunca forzados.
- Ritmo descriptivo: pinta la escena antes de nombrarla, como los mejores documentales mexicanos.
- Referencia geografía, historia y cultura mexicana con orgullo y precisión.
- Canal: "México Oculto" — cada vídeo descubre algo que la mayoría de mexicanos nunca conoció de su propio país.

CTA DE RETENCIÓN OBLIGATORIO (en los primeros 25-30 segundos del cuerpo, primera o segunda frase):
Inmediatamente después del hook, la PRIMERA frase del cuerpo SIEMPRE debe incluir esta llamada a quedarse:
"Y quédate hasta el final, porque lo más impactante de esta historia viene al cierre, y cambia todo lo que creías saber."
(Adáptala al tema pero mantén la estructura: promesa específica + "cambia todo")

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

6. ESCRIBE PARA LA VOZ — REGLAS CRÍTICAS PARA XTTS (si las ignoras, la voz cortará frases):
   La voz IA (XTTS) procesa bloques de máx. 280 caracteres. Sus reglas de oro:
   - NUNCA escribas una frase de más de 20 palabras sin una coma o punto en medio. NUNCA.
   - Cada oración DEBE terminar en punto (.), exclamación (!) o interrogación (?). Sin excepciones.
   - Para pausas dramáticas: usa punto y nueva oración corta. NO uses guiones ni paréntesis (XTTS los ignora).
   - Frases de 3-8 palabras = la voz las da con FUERZA e impacto natural.
   - Frases de 1-3 palabras solas = énfasis máximo. ("Silencio. Nada más.")
   - Frase larga envolvente (15-20 palabras MAX) = inmersión. Pero siempre con coma interna.
   - SUBE intensidad: frases cada vez más cortas hacia el clímax de cada bloque.
   PATRÓN MAESTRO: Frase corta. Frase corta. Frase media con, coma interna, que construye. Cortísima.
   NUNCA 3 frases seguidas del mismo largo.
   Que se lea como el mejor narrador de YouTube: subidas, bajadas, golpes, respiraciones.

7. RETENCIÓN CONTINUA — RE-ENGANCHA CADA 60-90 SEGUNDOS (técnica de los mejores canales):
   Los mejores canales no solo tienen un buen hook. Re-enganchan CADA 60-90 SEGUNDOS.
   - MICRO-PATTERN INTERRUPT cada 90 segundos: una pregunta directa al espectador, un dato
     que contradice lo que acaba de escuchar, o una frase corta inesperada que rompe el ritmo.
     Ejemplos: "Espera. Eso no es lo más perturbador." / "¿Y sabes qué es lo más extraño?"
     / "Pero hay algo que nadie menciona." / "Aquí es donde todo cambia."
   - CLIFFHANGER al final de CADA PÁRRAFO: nunca cierres una idea sin dejar un hilo abierto.
   - ADELANTOS (forward references): siembra promesas que cumplirás después.
     "En unos minutos entenderás por qué este detalle lo cambia todo."
   - CURIOSIDAD ENCADENADA: cada respuesta abre una nueva pregunta inmediatamente.
   - PATRÓN MRBEAST: cubre múltiples revelaciones por minuto en vez de desarrollar una lentamente.
   El objetivo: en NINGÚN momento de los 30+ minutos el espectador sienta que "ya puede irse".

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
