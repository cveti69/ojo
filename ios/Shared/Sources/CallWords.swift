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
        greeting: "Привет, чем помочь?",
        pause: ["Сейчас подумаю.", "Секунду.", "Так, дай подумать.", "Минутку.", "Сейчас скажу.", "Погоди секунду.",
                "Момент.", "Сейчас.", "Дай соображу.", "Сейчас прикину.", "Уже разбираюсь.", "Секундочку.", "Ага, сейчас.",
                "Один момент.", "Уже думаю.", "Скоро отвечу."],
        sounds: ["Угу.", "Ага.", "Так.", "Мм."],
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
        frame: "Рамка",
        frameLace: "Кружево",
        frameGlow: "Мягкий свет",
        frameTicks: "Чёрточки",
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
            CallQuestion("Что OjO видит и слышит?", "Твой экран, только пока ты его показываешь, и твой голос во время звонка. Они идут на наш сервер и в ИИ Meta. То, что говорит OjO, идёт в ElevenLabs для голоса. Пока идут тесты, мы храним слова и экраны из звонков, чтобы исправлять ошибки. Экраны оплаты, которые распознаём, не храним."),
            CallQuestion("Как сделать, чтобы он не смотрел?", "Скажи «Не смотри». Глаза закроются, и экран перестанет до него доходить. Скажи «Смотри», когда будет можно."),
            CallQuestion("А пароли и карты?", "OjO видит то же, что и ты, поэтому скажи «Не смотри», прежде чем их вводить. Сам он их никогда не просит."),
            CallQuestion("Он может нажать или оплатить за меня?", "Нет. Нажимаешь всегда ты, и оплату подтверждаешь только ты. OjO показывает и говорит, где нажать."),
            CallQuestion("Как взять текст, который он написал?", "OjO скопирует его за тебя. Нажми на поле и выбери «Вставить»."),
            CallQuestion("Я не вижу маленькое окно", "Открой Настройки iPhone › Основные › Картинка в картинке и включи Автозапуск «Картинки в картинке». Нажми на окно, чтобы вернуться в OjO.")
        ]
    )

    public static let english = CallWords(
        greeting: "Hi, how can I help?",
        pause: ["Let me think.", "One second.", "Hold on.", "Just a moment.", "Let me see.", "Give me a second.",
                "One moment.", "One sec.", "Let me work that out.", "Let me think about that.", "Working on it.", "Just a second.",
                "Okay, one sec.", "One moment, please.", "Thinking it over.", "I'll tell you in a sec."],
        sounds: ["Mm-hm.", "Okay.", "Right.", "Got it."],
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
        frame: "Frame",
        frameLace: "Lace",
        frameGlow: "Soft light",
        frameTicks: "Ticks",
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
            CallQuestion("What does OjO see and hear?", "Your screen, only while you share it, and your voice during a call. They go to our server and Meta's AI. What OjO says goes to ElevenLabs for the voice. While we test, we keep the words and screens from calls to fix problems. Payment screens we recognize aren't kept."),
            CallQuestion("How do I make it look away?", "Say “Don't look.” Its eyes close and your screen stops reaching it. Say “Look” when you're ready."),
            CallQuestion("What about passwords and cards?", "OjO sees what you see, so say “Don't look” before you type them. It never asks for them."),
            CallQuestion("Can it tap or pay for me?", "No. You always do the tapping, and only you confirm a payment. It points and tells you where."),
            CallQuestion("How do I use the text it writes?", "OjO copies it for you. Tap the field and choose Paste."),
            CallQuestion("I don't see the small window", "Open iPhone Settings › General › Picture in Picture and turn on Start PiP Automatically. Tap the window to come back to OjO.")
        ]
    )

    public static let spanish = CallWords(
        greeting: "Hola, ¿en qué te ayudo?",
        pause: ["Déjame pensar.", "Un segundo.", "Espera.", "Un momento.", "A ver.", "Dame un segundo."],
        sounds: ["Ajá.", "Vale.", "Mm."],
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
        frame: "Marco",
        frameLace: "Encaje",
        frameGlow: "Luz suave",
        frameTicks: "Rayitas",
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
            CallQuestion("¿Qué ve y oye OjO?", "Tu pantalla, solo mientras la compartes, y tu voz durante la llamada. Van a nuestro servidor y a la IA de Meta. Lo que dice OjO va a ElevenLabs para la voz. Mientras hacemos pruebas, guardamos las palabras y las pantallas de las llamadas para arreglar problemas. Las pantallas de pago que reconocemos no se guardan."),
            CallQuestion("¿Cómo hago que no mire?", "Di «No mires». Cierra los ojos y tu pantalla deja de llegarle. Di «Mira» cuando quieras."),
            CallQuestion("¿Y las contraseñas y las tarjetas?", "OjO ve lo mismo que tú, así que di «No mires» antes de escribirlas. Nunca te las pide."),
            CallQuestion("¿Puede tocar o pagar por mí?", "No. Siempre tocas tú, y solo tú confirmas un pago. OjO señala y te dice dónde."),
            CallQuestion("¿Cómo uso el texto que escribe?", "OjO te lo copia. Toca el campo y elige Pegar."),
            CallQuestion("No veo la ventana pequeña", "Abre Ajustes del iPhone › General › Imagen dentro de imagen y activa Iniciar modo PiP automáticamente. Toca la ventana para volver a OjO.")
        ]
    )
}
