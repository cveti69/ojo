import Shared
import SwiftUI

struct SettingsScreen: View {
    @ObservedObject var model: CallViewModel

    var body: some View {
        let words = model.words
        Form {
            Section {
                NavigationLink(words.journal) { JournalScreen(model: model) }
            }
            Section {
                Picker(words.frame, selection: Binding(get: { model.frame }, set: { model.setFrame($0) })) {
                    Text(words.frameLace).tag(EyesFrame.lace)
                    Text(words.frameGlow).tag(EyesFrame.glow)
                    Text(words.frameTicks).tag(EyesFrame.ticks)
                }
                Picker(words.language, selection: Binding(get: { model.language }, set: { model.setLanguage($0) })) {
                    Text(words.phoneLanguage).tag("")
                    Text("Русский").tag("ru")
                    Text("English").tag("en")
                    Text("Español").tag("es")
                }
                Picker(words.tone, selection: Binding(get: { model.character }, set: { model.setCharacter($0) })) {
                    Text(words.toneWarm).tag(AgentCharacter.warm)
                    Text(words.toneStrict).tag(AgentCharacter.strict)
                    Text(words.toneBrief).tag(AgentCharacter.brief)
                    Text(words.tonePatient).tag(AgentCharacter.patient)
                    Text(words.toneOwn).tag(AgentCharacter.own)
                }
                if model.character == .own {
                    TextField(words.toneOwnHint, text: Binding(get: { model.ownCharacter }, set: { model.setOwnCharacter($0) }), axis: .vertical)
                        .lineLimit(2...5)
                }
                if !model.voices.isEmpty {
                    Picker(words.voice, selection: Binding(get: { model.voice }, set: { model.setVoice($0) })) {
                        ForEach(model.voices) { Text($0.name).tag($0.id) }
                    }
                }
            }
        }
        .navigationTitle(words.settingsTitle)
        .task { await model.loadVoices() }
    }
}

/// What the phone wrote down about its calls: times and states, with no frames and no words.
private struct JournalScreen: View {
    @ObservedObject var model: CallViewModel
    @State private var log = ""
    @State private var copied = false

    var body: some View {
        Form {
            Section {
                Button(copied ? model.words.journalCopied : model.words.copyJournal) {
                    UIPasteboard.general.string = log
                    copied = true
                }
            }
            Section {
                Text(log)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
            }
        }
        .navigationTitle(model.words.journal)
        .task {
            while !Task.isCancelled {
                log = model.journal?.contents() ?? ""
                do { try await Task.sleep(nanoseconds: 1_000_000_000) } catch { break }
            }
        }
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
