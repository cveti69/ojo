import Shared
import SwiftUI

@MainActor
private final class SettingsForm: ObservableObject {
    @Published var serverAddress = ""
    @Published var token = ""
    @Published var userLang = "ru"
    @Published var targetLang = ""
    @Published var textEntry: TextEntry = .keyboard
    @Published var message = ""
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
                    Text("Клавиатура").tag(TextEntry.keyboard)
                    Text("Буфер обмена").tag(TextEntry.clipboard)
                }
            } header: {
                Text("Языки и ввод текста")
            } footer: {
                Text("auto — агент определит язык по экрану. Для приложений, которые запрещают сторонние клавиатуры, выберите буфер обмена. Новые настройки применяются со следующего звонка.")
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
        .onAppear {
            guard let settings = model.store?.settings else { return }
            form.serverAddress = settings.serverURL.absoluteString
            form.token = settings.token
            form.userLang = settings.userLang
            form.targetLang = settings.targetLang
            form.textEntry = settings.textEntry
        }
    }

    private func save() {
        do {
            try model.store?.save(serverAddress: form.serverAddress, token: form.token, userLang: form.userLang, targetLang: form.targetLang, textEntry: form.textEntry)
            form.message = "Сохранено в общей группе приложения"
            model.journal?.append("Настройки сохранены; text_entry: \(form.textEntry.rawValue)")
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
