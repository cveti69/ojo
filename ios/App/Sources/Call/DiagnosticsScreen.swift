import Shared
import SwiftUI

@MainActor
private final class DiagnosticsState: ObservableObject {
    @Published var log = ""
    @Published var copied = false
}

struct DiagnosticsScreen: View {
    @ObservedObject var model: CallViewModel
    @StateObject private var state = DiagnosticsState()

    var body: some View {
        Form {
            Section {
                Text("Нажмите «Тест окна», дождитесь готовности и перейдите в другое приложение. Окно должно открыться, номер и время — меняться каждые 3 секунды. Пауза/плей повторяет номер голосом. Вернитесь и нажмите «Завершить».")
                Text("«Тест буфера»: нажмите и сразу перейдите в другое приложение. Через 8 секунд вставьте текст в поле. Вернитесь: в журнале должны быть результат записи и слова «приложение в фоне».")
                Text("«Тест текста» читает нарисованный кадр сразу и через 8 секунд. Уйдите в другое приложение; затем проверьте в журнале время, число слов, фон и память.")
            }
            Section("Окно и звук") {
                Text(model.windowState.message)
                Text("Аудиосессия активна: \(model.audioIsActive ? "да" : "нет")")
                Toggle("Запасной путь окна (видеозвонок)", isOn: Binding(
                    get: { model.useVideoCallFallback }, set: { model.setVideoCallFallback($0) }
                ))
                Text("При переключении текущее окно закроется. Дождитесь готовности и откройте его снова.")
                    .font(.footnote).foregroundStyle(.secondary)
                if model.useVideoCallFallback {
                    Text("В запасном пути системных паузы/плей нет. Их проверяют с выключенным переключателем.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Button("Тест окна") { model.startWindowTest() }
                    .disabled(model.isTestingWindow)
                Button("Открыть окно") { model.openWindow() }
                    .disabled(!model.canOpenWindow)
                Button("Завершить", role: .destructive) { model.endCall() }
                    .disabled(!model.isCallActive && !model.isWorking)
                Button(model.isClipboardTestPending ? "Ждём 8 секунд — уйдите из приложения" : "Тест буфера") {
                    model.testClipboard()
                }
                .disabled(model.isClipboardTestPending)
            }
            Section("Текст экрана") {
                Picker("Где читать текст", selection: Binding(get: { model.screenTextMode }, set: { model.setScreenTextMode($0) })) {
                    Text("В приложении").tag(ScreenTextMode.app)
                    Text("В расширении записи").tag(ScreenTextMode.extension)
                    Text("Выключено").tag(ScreenTextMode.off)
                }
                Text("Обычно выбирайте приложение. Чтение в расширении проверяет работу при его лимите памяти.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button(model.isTextTestPending ? "Тест текста идёт — уйдите из приложения" : "Тест текста") { model.testScreenText() }
                    .disabled(model.isTextTestPending || model.configuration == nil)
            }
            Section("Журнал на телефоне") {
                Button(state.copied ? "Журнал скопирован" : "Скопировать журнал") {
                    state.log = model.journal?.contents() ?? "Журнал недоступен"
                    UIPasteboard.general.string = state.log
                    state.copied = true
                }
                Text(state.log)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
            }
        }
        .navigationTitle("Проверка")
        .task {
            while !Task.isCancelled {
                state.log = model.journal?.contents() ?? "Журнал недоступен"
                do { try await Task.sleep(nanoseconds: 1_000_000_000) } catch { break }
            }
        }
    }
}
