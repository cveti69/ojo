import ReplayKit
import SwiftUI

struct CallScreen: View {
    @StateObject private var model = CallViewModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        VStack(spacing: 12) {
            HintWindowPreview(window: model.window)
                .frame(width: 144, height: 192)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .accessibilityLabel("Предпросмотр подсказки")
            Text(model.windowState.message)
                .font(.caption)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            NavigationStack {
                Form {
                    Section("Статус") {
                        Text(model.status)
                        Text(model.lastInstruction).foregroundStyle(.secondary)
                        if model.needsUserConfirm {
                            Label(
                                "Проверьте действие и подтвердите его самостоятельно в приложении",
                                systemImage: "hand.raised.fill"
                            )
                            .foregroundStyle(.orange)
                        }
                    }
                    Section {
                        HStack {
                            Button("Позвонить") { model.startCall() }
                                .disabled(model.isCallActive || model.isWorking)
                            Spacer()
                            Button("Завершить", role: .destructive) { model.endCall() }
                                .disabled(!model.isCallActive && !model.isWorking)
                        }
                        .buttonStyle(.bordered)
                        if model.isRecording {
                            Label("Слушаю. После фразы помолчите 1,2 секунды", systemImage: "mic.fill")
                            Button("Закончить запись") { model.finishRecording() }
                        }
                        HStack {
                            BroadcastPicker(isEnabled: model.isCallActive && model.hasGoal)
                                .frame(width: 44, height: 44)
                            Text("Трансляция экрана")
                        }
                        Button("Открыть окно") { model.openWindow() }
                            .disabled(!model.canOpenWindow)
                    } header: {
                        Text("Звонок")
                    } footer: {
                        Text(
                            "Позвоните, произнесите цель, включите трансляцию и перейдите в нужное приложение. Все нажатия и подтверждения делаете вы."
                        )
                    }
                    Section("Цель текстом — запасной путь") {
                        TextField("Например: помоги заказать шампунь", text: $model.goal, axis: .vertical)
                            .lineLimit(1...4)
                        if !model.recognizedText.isEmpty {
                            Text("Распознано: \(model.recognizedText)")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Button("Отправить цель") { model.submitGoal() }
                            .disabled(
                                !model.isCallActive || model.isTestingWindow
                                    || model.goal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Button("Сказать цель ещё раз") { model.listenAgain() }
                            .disabled(!model.isCallActive || model.isTestingWindow || model.isWorking)
                    }
                    Section {
                        NavigationLink("Настройки") {
                            SettingsScreen(model: model)
                        }
                        NavigationLink("Проверка") {
                            DiagnosticsScreen(model: model)
                        }
                    }
                }
                .scrollDismissesKeyboard(.interactively)
                .navigationTitle(model.displayName)
                .navigationBarTitleDisplayMode(.inline)
            }
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            let name: String
            switch phase {
            case .active: name = "активна"
            case .inactive: name = "неактивна"
            case .background: name = "фон"
            @unknown default: name = "неизвестна"
            }
            model.sceneChanged(name, isBackground: phase == .background)
        }
    }
}

private struct HintWindowPreview: UIViewRepresentable {
    let window: any HintWindowPresenting
    func makeUIView(context: Context) -> UIView { window.inlineView }
    func updateUIView(_ uiView: UIView, context: Context) {}
}

private struct BroadcastPicker: UIViewRepresentable {
    let isEnabled: Bool

    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 44, height: 44))
        picker.preferredExtension = Bundle.main.bundleIdentifier.map { "\($0).broadcast" }
        picker.showsMicrophoneButton = false
        picker.accessibilityLabel = "Начать трансляцию экрана"
        return picker
    }

    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {
        uiView.isUserInteractionEnabled = isEnabled
        uiView.alpha = isEnabled ? 1 : 0.4
    }
}
