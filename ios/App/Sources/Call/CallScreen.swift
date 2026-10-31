import ReplayKit
import Shared
import SwiftUI

struct CallScreen: View {
    @StateObject private var model = CallViewModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var showsMore = false

    var body: some View {
        VStack(spacing: 20) {
            HStack {
                Spacer()
                Button { showsMore = true } label: {
                    Image(systemName: "ellipsis.circle").font(.title2)
                }
                .accessibilityLabel("Ещё")
            }
            Spacer(minLength: 0)
            Hosted(view: model.window.inlineView)
                .frame(width: 168, height: 224)
                .overlay {
                    if !model.isCallActive {
                        ZStack {
                            Color(.secondarySystemBackground)
                            Image(systemName: "phone.fill").font(.largeTitle).foregroundStyle(.secondary)
                        }
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .accessibilityLabel("Предпросмотр подсказки")
            VStack(spacing: 8) {
                Text(title)
                    .font(.title2.weight(.semibold))
                    .minimumScaleFactor(0.7)
                if model.status != title {  // The agent says what it shows, so both lines can be the same.
                    Text(model.status)
                        .foregroundStyle(.secondary)
                }
            }
            .multilineTextAlignment(.center)
            Spacer(minLength: 0)
            actions
        }
        .padding(16)
        .sheet(isPresented: $showsMore) { MoreScreen(model: model) }
        .task(id: model.isGreeted) {
            guard model.isGreeted, !model.isSharing, scenePhase == .active else { return }
            showsMore = false
            do { try await Task.sleep(nanoseconds: 700_000_000) } catch { return }  // Our sheet has to close first.
            model.askForScreen()
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            let name: String
            switch phase {
            case .active: name = "активна"
            case .inactive: name = "неактивна"
            case .background: name = "фон"
            @unknown default: name = "неизвестна"
            }
            model.sceneChanged(name, isActive: phase == .active)
        }
    }

    private var title: String {
        switch model.phase {
        case .idle, .busy: return model.displayName
        case .needsScreen: return "Покажи экран"
        case .guiding: return model.lastInstruction
        }
    }

    @ViewBuilder private var actions: some View {
        VStack(spacing: 12) {
            if model.phase == .needsScreen {
                Label("Покажи экран", systemImage: "rectangle.on.rectangle")
                    .callButton(.accentColor)
                    .overlay { Hosted(view: model.screenShare) }
            }
            if model.windowFailed {
                Button("Открыть окно") { model.openWindow() }
            }
            if model.phase == .idle {
                Button { model.startCall() } label: {
                    Label("Позвонить", systemImage: "phone.fill").callButton(.green)
                }
            } else {
                Button(role: .destructive) { model.endCall() } label: {
                    Label("Завершить", systemImage: "phone.down.fill").callButton(.red)
                }
            }
        }
    }
}

private extension View {
    func callButton(_ color: Color) -> some View {
        font(.headline)
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(color, in: RoundedRectangle(cornerRadius: 16))
    }
}

/// Everything a call can do without being needed in a usual one.
private struct MoreScreen: View {
    @ObservedObject var model: CallViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Написать текстом") {
                    TextField("Что сказать агенту", text: $model.typed, axis: .vertical)
                        .lineLimit(1...4)
                    Button("Отправить") {
                        model.sendTyped()
                        dismiss()
                    }
                    .disabled(
                        !model.isCallActive || model.isTestingWindow
                            || model.typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                Section {
                    HStack {
                        BroadcastPicker(isEnabled: model.isGreeted)
                            .frame(width: 44, height: 44)
                        Text("Трансляция экрана")
                    }
                    Button("Открыть окно") { model.openWindow() }
                        .disabled(!model.canOpenWindow)
                    Text(model.windowState.message)
                        .font(.footnote).foregroundStyle(.secondary)
                } header: {
                    Text("Звонок")
                } footer: {
                    Text("Все нажатия и подтверждения делаете вы.")
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
            .navigationTitle("Ещё")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Готово") { dismiss() }
                }
            }
        }
    }
}

private struct Hosted: UIViewRepresentable {
    let view: UIView
    func makeUIView(context: Context) -> UIView { view }
    func updateUIView(_ uiView: UIView, context: Context) {}
}

/// The system's own picker, stretched over our button so a real tap lands on Apple's control.
final class ScreenSharePicker: RPSystemBroadcastPickerView {
    override init(frame: CGRect) {
        super.init(frame: frame)
        preferredExtension = Bundle.main.bundleIdentifier.map { "\($0).broadcast" }
        showsMicrophoneButton = false
        alpha = 0.02  // Not seen over our button. Below 0.01 it would stop taking taps.
        accessibilityLabel = "Показать экран"
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func layoutSubviews() {
        super.layoutSubviews()
        subviews.forEach { $0.frame = bounds }
    }

    func open() -> Bool {
        guard let button = subviews.compactMap({ $0 as? UIButton }).first else { return false }
        button.sendActions(for: .touchUpInside)
        return true
    }
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
