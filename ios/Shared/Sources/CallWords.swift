import Foundation

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

    // In the settings.
    public let settingsTitle: String
    public let journal: String
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
    public let voice: String
    public let done: String

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
        settingsTitle: "Настройки",
        journal: "Журнал",
        copyJournal: "Скопировать журнал",
        journalCopied: "Журнал скопирован",
        frame: "Рамка",
        frameLace: "Кружево",
        frameGlow: "Мягкий свет",
        frameTicks: "Чёрточки",
        language: "Язык",
        phoneLanguage: "Как на телефоне",
        tone: "Тон разговора",
        toneWarm: "Тёплый",
        toneStrict: "Строгий",
        toneBrief: "Очень краткий",
        tonePatient: "Терпеливый",
        toneOwn: "Свой текст",
        toneOwnHint: "Каким быть агенту",
        voice: "Голос",
        done: "Готово"
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
        hangUp: "End call",
        settingsTitle: "Settings",
        journal: "Log",
        copyJournal: "Copy the log",
        journalCopied: "Log copied",
        frame: "Frame",
        frameLace: "Lace",
        frameGlow: "Soft light",
        frameTicks: "Ticks",
        language: "Language",
        phoneLanguage: "Same as the phone",
        tone: "Tone of the call",
        toneWarm: "Warm",
        toneStrict: "Strict",
        toneBrief: "Very brief",
        tonePatient: "Patient",
        toneOwn: "My own words",
        toneOwnHint: "What the agent should be like",
        voice: "Voice",
        done: "Done"
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
        settingsTitle: "Ajustes",
        journal: "Registro",
        copyJournal: "Copiar el registro",
        journalCopied: "Registro copiado",
        frame: "Marco",
        frameLace: "Encaje",
        frameGlow: "Luz suave",
        frameTicks: "Rayitas",
        language: "Idioma",
        phoneLanguage: "Como en el teléfono",
        tone: "Tono de la llamada",
        toneWarm: "Cálido",
        toneStrict: "Estricto",
        toneBrief: "Muy breve",
        tonePatient: "Paciente",
        toneOwn: "Texto propio",
        toneOwnHint: "Cómo debe ser el agente",
        voice: "Voz",
        done: "Listo"
    )
}
