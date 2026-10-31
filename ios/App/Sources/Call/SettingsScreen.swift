import Shared
import SwiftUI

@MainActor
private final class SettingsForm: ObservableObject {
    @Published var serverAddress = ""
    @Published var token = ""
    @Published var userLang = "ru"
    @Published var targetLang = ""
    @Published var textEntry: TextEntry = .clipboard
    @Published var thinking: ThinkingLevel = .auto
    @Published var character: AgentCharacter = .warm
    @Published var ownCharacter = ""
    @Published var message = ""
}

private extension ThinkingLevel {
    var title: String {
        switch self {
        case .auto: return "Авто"
        case .minimal: return "Минимальные"
        case .low: return "Низкие"
        case .medium: return "Средние"
        case .high: return "Высокие"
        case .xhigh: return "Очень высокие"
        case .max: return "Максимальные"
        }
    }
}

private extension AgentCharacter {
    var title: String {
        switch self {
        case .warm: return "Тёплый"
        case .strict: return "Строгий"
        case .brief: return "Очень краткий"
        case .patient: return "Терпеливый"
        case .own: return "Свой текст"
        }
    }
}

struct SettingsScreen: View {
    @ObservedObject var model: CallViewModel
    @StateObject private var form = SettingsForm()

    var body: some View {
        Form {
            Section("Сервер") {
                TextField("Адрес сервера", text: $form.serverAddress)
                    .textContentType(.URL).keyboardType(.URL)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("Токен X-Token", text: $form.token)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            Section {
                TextField("Ваш язык", text: $form.userLang)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("Язык приложения-цели: id, auto, en…", text: $form.targetLang)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                Picker("Способ ввода", selection: $form.textEntry) {
                    Text("Буфер обмена").tag(TextEntry.clipboard)
                    Text("Наша клавиатура").tag(TextEntry.keyboard)
                }
            } header: {
                Text("Языки и ввод текста")
            } footer: {
                Text("auto — агент определит язык по экрану. Буфер работает везде. Нашу клавиатуру включайте только для приложений, которые её пускают. Новые настройки применяются со следующего звонка.")
            }
            Section {
                Picker("Рассуждения", selection: $form.thinking) {
                    ForEach(ThinkingLevel.allCases, id: \.self) { Text($0.title).tag($0) }
                }
                Picker("Характер", selection: $form.character) {
                    ForEach(AgentCharacter.allCases, id: \.self) { Text($0.title).tag($0) }
                }
                if form.character == .own {
                    TextField("Каким быть агенту", text: $form.ownCharacter, axis: .vertical)
                        .lineLimit(2...5)
                }
            } header: {
                Text("Агент")
            } footer: {
                Text("Авто: глубину выбирает сервер. Чем глубже рассуждения, тем дольше ждать ответа. Запреты на пароли, карты и оплату характер не меняет. Новые настройки применяются со следующего звонка.")
            }
            if !model.voices.isEmpty {
                Section {
                    Picker("Голос", selection: Binding(get: { model.voice }, set: { model.setVoice($0) })) {
                        ForEach(model.voices) { Text($0.name).tag($0.id) }
                    }
                } footer: {
                    Text("Новый голос работает со следующей реплики.")
                }
            }
            Section {
                Toggle("Глазки", isOn: Binding(get: { model.showsEyes }, set: { model.setShowsEyes($0) }))
                    .disabled(model.isCallActive)
                NavigationLink("Показать глазки") { EyesPreviewScreen() }
            } footer: {
                Text("Глаза в окне между подсказками: показывают, что агент смотрит, думает или слушает. Включаются со следующего звонка.")
            }
            Section {
                Button("Сохранить") { save() }
                    .disabled(model.store == nil)
                if !form.message.isEmpty { Text(form.message) }
                NavigationLink("Подключить и проверить клавиатуру") {
                    KeyboardOnboardingScreen(displayName: model.displayName)
                }
            }
        }
        .navigationTitle("Настройки")
        .task { await model.loadVoices() }
        .onAppear {
            guard let settings = model.store?.settings else { return }
            form.serverAddress = settings.serverURL.absoluteString
            form.token = settings.token
            form.userLang = settings.userLang
            form.targetLang = settings.targetLang
            form.textEntry = settings.textEntry
            form.thinking = settings.thinking
            form.character = settings.character
            form.ownCharacter = settings.ownCharacter
        }
    }

    private func save() {
        do {
            try model.store?.save(serverAddress: form.serverAddress, token: form.token, userLang: form.userLang, targetLang: form.targetLang, textEntry: form.textEntry,
                                  thinking: form.thinking, character: form.character, ownCharacter: form.ownCharacter)
            form.message = "Сохранено в общей группе приложения"
            model.journal?.append("Настройки сохранены; text_entry: \(form.textEntry.rawValue); рассуждения: \(form.thinking.rawValue); характер: \(form.character.rawValue)")
        } catch { form.message = error.localizedDescription }
    }
}

private struct KeyboardOnboardingScreen: View {
    let displayName: String
    @Environment(\.openURL) private var openURL
    @StateObject private var field = KeyboardTestField()

    var body: some View {
        Form {
            Section("Подключение") {
                Text("Настройки → Основные → Клавиатура → Клавиатуры → Новые клавиатуры → \(displayName) → «Разрешить полный доступ».")
                Text("Вернитесь сюда, коснитесь тестового поля и выберите \(displayName) кнопкой глобуса. Во время звонка появятся готовые тексты от агента.")
                Button("Открыть настройки") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
            }
            Section("Проверка клавиатуры") {
                TextField("Коснитесь и переключитесь глобусом", text: $field.text, axis: .vertical)
                    .lineLimit(2...5)
                Text("Для проверки перевода наберите фразу обычной клавиатурой и переключитесь обратно. Нужны активная сессия и полный доступ.")
                    .font(.footnote)
            }
        }
        .navigationTitle("Клавиатура")
    }
}

@MainActor
private final class KeyboardTestField: ObservableObject {
    @Published var text = ""
}
