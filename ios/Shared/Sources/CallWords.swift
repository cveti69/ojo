import Foundation

/// One question on the questions screen.
public struct CallQuestion: Sendable, Equatable {
    public let title: String
    public let answer: String

    init(_ title: String, _ answer: String) {
        self.title = title
        self.answer = answer
    }
}

/// What the app says and shows by itself on a call, in the language of the phone. The agent's own lines come from the model, so on
/// a phone in any other language the agent goes on in that language and only these words are English.
public struct CallWords: Sendable, Equatable {
    // Heard on the call.
    public let greeting: String
    public let pause: [String]
    public let sounds: [String]
    public let failed: String
    public let lostHearing: String
    public let noHintYet: String

    // In the window.
    public let rest: String
    public let listening: String
    public let looking: String
    public let thinking: String
    public let speaking: String
    public let noLink: String
    public let longWait: String
    public let notLooking: String
    public let waitingHint: String

    // On the call screen.
    public let ready: String
    public let noHints: String
    public let connecting: String
    public let greets: String
    public let talk: String
    public let ended: String
    public let openApp: String
    public let refused: String
    public let interrupted: String
    public let endedByInterruption: String
    public let goesOn: String
    public let notResumed: String
    public let showScreen: String
    public let openWindow: String
    public let call: String
    public let hangUp: String
    public let soundSpeaker: String
    public let soundHeadphones: String
    public let chooseSound: String
    public let mute: String
    public let muted: String
    public let muteMicrophone: String
    public let unmuteMicrophone: String

    // In the settings.
    public let settingsTitle: String
    public let copyJournal: String
    public let journalCopied: String
    public let frame: String
    public let frameLace: String
    public let frameGlow: String
    public let frameTicks: String
    public let fastMode: String
    public let nextStepTest: String
    public let yourData: String
    public let language: String
    public let phoneLanguage: String
    public let tone: String
    public let toneWarm: String
    public let toneStrict: String
    public let toneBrief: String
    public let tonePatient: String
    public let toneOwn: String
    public let toneOwnHint: String
    public let toneOwnExample: String
    public let voice: String
    public let back: String

    // On the questions screen.
    public let questionsTitle: String
    public let questions: [CallQuestion]

    public static var device: CallWords { of(Locale.preferredLanguages.first ?? "en") }

    public static func of(_ language: String) -> CallWords {
        switch language.lowercased().prefix(while: \.isLetter) {
        case "ru": return russian
        case "es": return spanish
        default: return english
        }
    }

    public static let russian = CallWords(
        greeting: "Чем помочь?",
        pause: ["Сейчас подумаю.", "Секунду.", "Минутку.", "Сейчас скажу.", "Погоди секунду.", "Момент.", "Дай соображу.",
                "Сейчас прикину.", "Уже разбираюсь.", "Секундочку.", "Один момент.", "Уже думаю.", "Скоро отвечу."],
        sounds: ["Хм."],
        failed: "Не получилось ответить, повтори, пожалуйста.",
        lostHearing: "Я перестал тебя слышать. Открой наше приложение на секунду, и снова услышу.",
        noHintYet: "Пока нет подсказки. Ожидайте.",
        rest: "Я рядом",
        listening: "Слушаю",
        looking: "Смотрю",
        thinking: "Думаю",
        speaking: "Говорю",
        noLink: "Нет связи",
        longWait: "Всё ещё думаю",
        notLooking: "Не смотрю",
        waitingHint: "Ожидание подсказки",
        ready: "Позвоните и скажите, что нужно. Нажимаете всегда вы.",
        noHints: "Подсказок пока нет",
        connecting: "Соединяемся…",
        greets: "Агент здоровается",
        talk: "Говорите, как по телефону",
        ended: "Звонок завершён",
        openApp: "Открой нужное приложение",
        refused: "Сервер отклонил сессию или токен. Проверьте настройки и позвоните снова",
        interrupted: "Звонок прерван системой",
        endedByInterruption: "Звонок завершён после прерывания",
        goesOn: "Звонок продолжается",
        notResumed: "Не удалось возобновить звонок",
        showScreen: "Покажи экран",
        openWindow: "Открыть окно",
        call: "Позвонить",
        hangUp: "Завершить",
        soundSpeaker: "Динамик",
        soundHeadphones: "Наушники",
        chooseSound: "Выбрать, куда идёт звук",
        mute: "Выключить",
        muted: "Выключен",
        muteMicrophone: "Выключить микрофон",
        unmuteMicrophone: "Включить микрофон",
        settingsTitle: "Настройки",
        copyJournal: "Скопировать журнал",
        journalCopied: "Журнал скопирован",
        frame: "Вид окна",
        frameLace: "Кружево",
        frameGlow: "Мягкий свет",
        frameTicks: "Чёрточки",
        fastMode: "Турбо",
        nextStepTest: "Меньше шагов",
        yourData: "Ваши данные",
        language: "Язык",
        phoneLanguage: "Авто",
        tone: "Тон",
        toneWarm: "Тёплый",
        toneStrict: "Строгий",
        toneBrief: "Очень краткий",
        tonePatient: "Терпеливый",
        toneOwn: "Свой текст",
        toneOwnHint: "Каким быть OjO",
        toneOwnExample: "Спокойный, немного с юмором",
        voice: "Голос",
        back: "Назад",
        questionsTitle: "Вопросы",
        questions: [
            CallQuestion("Что OjO видит и слышит?", "Твой экран, только пока ты его показываешь, и твой голос во время звонка. Они идут на наш сервер и в ИИ Meta. То, что говорит OjO, идёт в ElevenLabs для голоса. После звонка мы ничего не храним."),
            CallQuestion("Как сделать, чтобы он не смотрел?", "Скажи «Не смотри», например перед тем как вводить пароль или номер карты. Глаза закроются, и iPhone перестанет отправлять твой экран. Скажи «Смотри», когда будет можно. Пароли и карты OjO никогда не просит."),
            CallQuestion("Он может нажать или оплатить за меня?", "Нет. Нажимаешь всегда ты, и оплату подтверждаешь только ты. OjO показывает и говорит, где нажать."),
            CallQuestion("Как взять текст, который он написал?", "OjO скопирует его за тебя. Нажми на поле и выбери «Вставить»."),
            CallQuestion("Что такое Турбо?", "Быстрый режим. OjO отвечает моделью Muse Spark: она быстрее, но на сложных экранах думает меньше. Включается в настройках."),
            CallQuestion("Какие языки он знает?", "OjO говорит по-английски или на языке телефона, если в «Языке» выбрано «Авто». Свои короткие фразы он говорит по-английски, по-русски или по-испански. Лучше всего он читает экраны на китайском, японском, корейском, тайском, вьетнамском, арабском, русском и на языках с латинскими буквами: это языки, которые читает iPhone."),
            CallQuestion("Я не вижу маленькое окно", "Открой Настройки iPhone › Основные › Картинка в картинке и включи Автозапуск «Картинки в картинке». Нажми на окно, чтобы вернуться в OjO.")
        ]
    )

    public static let english = CallWords(
        greeting: "How can I help?",
        pause: ["Let me think.", "One second.", "Hold on.", "Just a moment.", "Let me see.", "Give me a second.",
                "One moment.", "One sec.", "Let me work that out.", "Let me think about that.", "Working on it.", "Just a second.",
                "One moment, please.", "Thinking it over.", "I'll tell you in a sec."],
        sounds: ["Hmm.", "Let's see."],
        failed: "Sorry, I couldn't answer. Please say that again.",
        lostHearing: "I can't hear you anymore. Open our app for a second and I'll hear you again.",
        noHintYet: "No hint yet. Please wait.",
        rest: "I'm here",
        listening: "Listening",
        looking: "Looking",
        thinking: "Thinking",
        speaking: "Speaking",
        noLink: "No connection",
        longWait: "Still thinking",
        notLooking: "Not looking",
        waitingHint: "Waiting for a hint",
        ready: "Call and say what you need. You always do the tapping.",
        noHints: "No hints yet",
        connecting: "Connecting…",
        greets: "The agent is saying hello",
        talk: "Talk as you would on the phone",
        ended: "Call ended",
        openApp: "Open the app you need",
        refused: "The server refused the session or token. Check the settings and call again",
        interrupted: "The system interrupted the call",
        endedByInterruption: "The call ended after an interruption",
        goesOn: "The call goes on",
        notResumed: "Couldn't resume the call",
        showScreen: "Show your screen",
        openWindow: "Open the window",
        call: "Call",
        hangUp: "End",
        soundSpeaker: "Speaker",
        soundHeadphones: "Headphones",
        chooseSound: "Choose where the sound goes",
        mute: "Mute",
        muted: "Muted",
        muteMicrophone: "Mute the microphone",
        unmuteMicrophone: "Unmute the microphone",
        settingsTitle: "Settings",
        copyJournal: "Copy the log",
        journalCopied: "Log copied",
        frame: "Window style",
        frameLace: "Lace",
        frameGlow: "Soft light",
        frameTicks: "Ticks",
        fastMode: "Turbo",
        nextStepTest: "Fewer steps",
        yourData: "Your data",
        language: "Language",
        phoneLanguage: "Auto",
        tone: "Tone",
        toneWarm: "Warm",
        toneStrict: "Strict",
        toneBrief: "Very brief",
        tonePatient: "Patient",
        toneOwn: "My own words",
        toneOwnHint: "What OjO should be like",
        toneOwnExample: "Calm, a little funny",
        voice: "Voice",
        back: "Back",
        questionsTitle: "Questions",
        questions: [
            CallQuestion("What does OjO see and hear?", "Your screen, only while you share it, and your voice during a call. They go to our server and Meta's AI. What OjO says goes to ElevenLabs for the voice. We keep nothing after the call."),
            CallQuestion("How do I make it look away?", "Say “Don't look,” for example before you type a password or a card number. Its eyes close and your iPhone stops sending your screen. Say “Look” when you're ready. OjO never asks for passwords or cards."),
            CallQuestion("Can it tap or pay for me?", "No. You always do the tapping, and only you confirm a payment. It points and tells you where."),
            CallQuestion("How do I use the text it writes?", "OjO copies it for you. Tap the field and choose Paste."),
            CallQuestion("What is Turbo?", "A faster mode. OjO answers with Muse Spark, a quicker Meta model that thinks less on hard screens. Turn it on in Settings."),
            CallQuestion("Which languages does it know?", "OjO talks in English, or in your phone's language when Language is Auto. Its own short phrases are in English, Russian or Spanish. It reads screens best in Chinese, Japanese, Korean, Thai, Vietnamese, Arabic, Russian and languages written in Latin letters: the ones your iPhone can read."),
            CallQuestion("I don't see the small window", "Open iPhone Settings › General › Picture in Picture and turn on Start PiP Automatically. Tap the window to come back to OjO.")
        ]
    )

    public static let spanish = CallWords(
        greeting: "¿En qué te ayudo?",
        pause: ["Déjame pensar.", "Un segundo.", "Espera.", "Un momento.", "A ver.", "Dame un segundo."],
        sounds: ["A ver."],
        failed: "No pude responder. Repítelo, por favor.",
        lostHearing: "Ya no te oigo. Abre nuestra app un segundo y volveré a oírte.",
        noHintYet: "Aún no hay ninguna pista. Espera un momento.",
        rest: "Aquí estoy",
        listening: "Te escucho",
        looking: "Mirando",
        thinking: "Pensando",
        speaking: "Hablando",
        noLink: "Sin conexión",
        longWait: "Sigo pensando",
        notLooking: "No estoy mirando",
        waitingHint: "Esperando una pista",
        ready: "Llama y di lo que necesitas. Siempre tocas tú.",
        noHints: "Aún no hay pistas",
        connecting: "Conectando…",
        greets: "El agente te saluda",
        talk: "Habla como por teléfono",
        ended: "Llamada terminada",
        openApp: "Abre la app que necesitas",
        refused: "El servidor rechazó la sesión o el token. Revisa los ajustes y vuelve a llamar",
        interrupted: "El sistema interrumpió la llamada",
        endedByInterruption: "La llamada terminó tras una interrupción",
        goesOn: "La llamada continúa",
        notResumed: "No se pudo reanudar la llamada",
        showScreen: "Muestra tu pantalla",
        openWindow: "Abrir la ventana",
        call: "Llamar",
        hangUp: "Colgar",
        soundSpeaker: "Altavoz",
        soundHeadphones: "Auriculares",
        chooseSound: "Elegir adónde va el sonido",
        mute: "Silenciar",
        muted: "Silenciado",
        muteMicrophone: "Silenciar el micrófono",
        unmuteMicrophone: "Activar el micrófono",
        settingsTitle: "Ajustes",
        copyJournal: "Copiar el registro",
        journalCopied: "Registro copiado",
        frame: "Estilo de ventana",
        frameLace: "Encaje",
        frameGlow: "Luz suave",
        frameTicks: "Rayitas",
        fastMode: "Turbo",
        nextStepTest: "Menos pasos",
        yourData: "Tus datos",
        language: "Idioma",
        phoneLanguage: "Automático",
        tone: "Tono",
        toneWarm: "Cálido",
        toneStrict: "Estricto",
        toneBrief: "Muy breve",
        tonePatient: "Paciente",
        toneOwn: "Texto propio",
        toneOwnHint: "Cómo debe ser OjO",
        toneOwnExample: "Tranquilo, con algo de humor",
        voice: "Voz",
        back: "Atrás",
        questionsTitle: "Preguntas",
        questions: [
            CallQuestion("¿Qué ve y oye OjO?", "Tu pantalla, solo mientras la compartes, y tu voz durante la llamada. Van a nuestro servidor y a la IA de Meta. Lo que dice OjO va a ElevenLabs para la voz. No guardamos nada después de la llamada."),
            CallQuestion("¿Cómo hago que no mire?", "Di «No mires», por ejemplo antes de escribir una contraseña o un número de tarjeta. Cierra los ojos y tu iPhone deja de enviar tu pantalla. Di «Mira» cuando quieras. OjO nunca te pide contraseñas ni tarjetas."),
            CallQuestion("¿Puede tocar o pagar por mí?", "No. Siempre tocas tú, y solo tú confirmas un pago. OjO señala y te dice dónde."),
            CallQuestion("¿Cómo uso el texto que escribe?", "OjO te lo copia. Toca el campo y elige Pegar."),
            CallQuestion("¿Qué es Turbo?", "Un modo más rápido. OjO responde con Muse Spark, un modelo de Meta más ágil que piensa menos en pantallas difíciles. Actívalo en Ajustes."),
            CallQuestion("¿Qué idiomas conoce?", "OjO habla en inglés, o en el idioma de tu teléfono si en Idioma eliges Automático. Sus frases cortas propias están en inglés, ruso o español. Lee mejor las pantallas en chino, japonés, coreano, tailandés, vietnamita, árabe, ruso y en idiomas con letras latinas: los que tu iPhone sabe leer."),
            CallQuestion("No veo la ventana pequeña", "Abre Ajustes del iPhone › General › Imagen dentro de imagen y activa Iniciar modo PiP automáticamente. Toca la ventana para volver a OjO.")
        ]
    )
}
