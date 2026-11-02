import Shared
import SwiftUI

struct SettingsScreen: View {
    @ObservedObject var model: CallViewModel
    let back: () -> Void
    @State private var copied = false

    var body: some View {
        let words = model.words
        VStack(spacing: 0) {
            PageHeader(title: words.settingsTitle, backLabel: words.back, back: back)
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    group(words.language) {
                        Pill(title: words.phoneLanguage, isOn: model.language == "") { model.setLanguage("") }
                        Pill(title: "English", isOn: model.language == "en") { model.setLanguage("en") }
                        Pill(title: "Русский", isOn: model.language == "ru") { model.setLanguage("ru") }
                        Pill(title: "Español", isOn: model.language == "es") { model.setLanguage("es") }
                    }
                    if !model.voices.isEmpty {
                        group(words.voice) {
                            ForEach(model.voices) { voice in
                                Pill(title: voice.name, isOn: model.voice == voice.id) { model.setVoice(voice.id) }
                            }
                        }
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        group(words.tone) {
                            Pill(title: words.toneWarm, isOn: model.character == .warm) { model.setCharacter(.warm) }
                            Pill(title: words.toneStrict, isOn: model.character == .strict) { model.setCharacter(.strict) }
                            Pill(title: words.toneBrief, isOn: model.character == .brief) { model.setCharacter(.brief) }
                            Pill(title: words.tonePatient, isOn: model.character == .patient) { model.setCharacter(.patient) }
                            Pill(title: words.toneOwn, isOn: model.character == .own) { model.setCharacter(.own) }
                        }
                        if model.character == .own { ownTone }
                    }
                    group(words.frame) {
                        Pill(title: words.frameLace, isOn: model.frame == .lace) { model.setFrame(.lace) }
                        Pill(title: words.frameGlow, isOn: model.frame == .glow) { model.setFrame(.glow) }
                        Pill(title: words.frameTicks, isOn: model.frame == .ticks) { model.setFrame(.ticks) }
                    }
                    CheckRow(title: words.noiseReduction, isOn: model.noiseReduction) { model.setNoiseReduction(!model.noiseReduction) }
                    CheckRow(title: words.fastMode, isOn: model.fastMode) { model.setFastMode(!model.fastMode) }
                    CheckRow(title: words.nextStepTest, isOn: model.nextStepTest) { model.setNextStepTest(!model.nextStepTest) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 28)
                .padding(.bottom, 20)
            }
            .scrollIndicators(.hidden)
            .scrollDismissesKeyboard(.interactively)
            Button {
                UIPasteboard.general.string = model.journal?.contents() ?? ""
                copied = true
            } label: {
                Text(copied ? words.journalCopied : words.copyJournal)
                    .font(.system(.subheadline, design: .rounded, weight: .medium))
                    .foregroundStyle(Palette.muted)
                    .padding(.horizontal, 16)
                    .frame(minHeight: 44)
            }
            .buttonStyle(.plain)
        }
        .padding(.top, 17)
        .padding(.horizontal, 20)
        .padding(.bottom, 14)
        .task { await model.loadVoices() }
    }

    private var ownTone: some View {
        VStack(alignment: .leading, spacing: 10) {
            caption(model.words.toneOwnHint)
            TextField(model.words.toneOwnExample, text: Binding(get: { model.ownCharacter }, set: { model.setOwnCharacter($0) }), axis: .vertical)
                .font(.system(.subheadline, design: .rounded))
                .foregroundStyle(.white)
                .lineLimit(1...5)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .frame(minHeight: 44)
                .overlay { RoundedRectangle(cornerRadius: 22).strokeBorder(Palette.line, lineWidth: 1.5) }
        }
    }

    private func group<Pills: View>(_ title: String, @ViewBuilder pills: () -> Pills) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            caption(title)
            Rows { pills() }
        }
    }

    private func caption(_ text: String) -> some View {
        Text(text)
            .font(.system(.footnote, design: .rounded, weight: .medium))
            .foregroundStyle(Palette.muted)
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
