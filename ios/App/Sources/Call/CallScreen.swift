import AVKit
import ReplayKit
import Shared
import SwiftUI

struct CallScreen: View {
    private static let faceAt = 213.0 / 763  // Of the height, on all three screens with the face.

    @StateObject private var model = CallViewModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var page: Page?

    private enum Page {
        case settings, questions
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            GeometryReader { space in
                VStack(spacing: 0) {
                    Color.clear.frame(height: space.size.height * Self.faceAt)
                    face
                    underFace
                        .padding(.top, 20)
                    Spacer(minLength: 0)
                    action
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 14)
                .overlay(alignment: .top) {
                    if model.phase == .idle { pages }
                }
            }
            .dynamicTypeSize(...DynamicTypeSize.accessibility1)
            .accessibilityHidden(page != nil)
            if let page {
                Group {
                    switch page {
                    case .settings: SettingsScreen(model: model) { self.page = nil }
                    case .questions: QuestionsScreen(words: model.words) { self.page = nil }
                    }
                }
                .dynamicTypeSize(...DynamicTypeSize.accessibility3)
                .background(Color.black.ignoresSafeArea())
                .transition(.move(edge: .trailing))
            }
        }
        .animation(.easeInOut(duration: 0.25), value: page)
        .preferredColorScheme(.dark)
        .tint(Palette.accent)
        .task(id: model.isGreeted) {
            guard model.isGreeted, !model.isSharing, scenePhase == .active else { return }
            do { try await Task.sleep(nanoseconds: 700_000_000) } catch { return }  // The button has to be in its place first.
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

    /// The system's window opens from the view behind the face, so that view stays in the app, covered, for the whole call.
    private var face: some View {
        Hosted(view: model.window.inlineView)
            .frame(width: 168, height: 224)
            .overlay(Color.black)
            .frame(width: 176, height: 168)
            .overlay {
                Face(pose: pose, blinks: !model.isNotLooking)
                    .accessibilityLabel(model.isNotLooking ? model.words.notLooking : model.displayName)
            }
    }

    /// Shut eyes mean one thing only: it is not looking now, as the person asked.
    private var pose: EyePose {
        if endedCall != nil { return .atTheButton }
        return model.isNotLooking ? .shut : EyePose()
    }

    private var endedCall: TimeInterval? { model.phase == .idle ? model.lastCall : nil }

    @ViewBuilder private var underFace: some View {
        if model.phase == .idle {
            VStack(spacing: 8) {
                if let endedCall {
                    Text(Self.clock(endedCall))
                        .font(.system(.largeTitle, design: .rounded, weight: .semibold))
                        .monospacedDigit()
                        .foregroundStyle(.white)
                }
                if let afterCall {
                    Text(afterCall)
                        .font(.system(.body, design: .rounded, weight: .medium))
                        .foregroundStyle(Palette.muted)
                }
            }
            .multilineTextAlignment(.center)
        } else {
            VStack(spacing: 20) {
                VStack(spacing: 8) {
                    TimelineView(.periodic(from: model.callBegan ?? .now, by: 1)) { moment in
                        Text(Self.clock(model.callBegan.map { moment.date.timeIntervalSince($0) } ?? 0))
                            .font(.system(.title3, design: .rounded, weight: .medium))
                            .monospacedDigit()
                            .foregroundStyle(Palette.muted)
                    }
                    if let trouble {
                        Text(trouble)
                            .font(.system(.subheadline, design: .rounded))
                            .foregroundStyle(Palette.muted)
                            .multilineTextAlignment(.center)
                    }
                }
                if model.phase == .needsScreen {
                    Text(model.words.showScreen)
                        .font(.system(.headline, design: .rounded))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 28)
                        .frame(minHeight: 52)
                        .overlay { Capsule().strokeBorder(.white, lineWidth: 1.5) }
                        .overlay { Hosted(view: model.screenShare) }
                        .padding(.top, 12)
                }
                if model.windowFailed {
                    Button(model.words.openWindow) { model.openWindow() }
                        .font(.system(.subheadline, design: .rounded, weight: .medium))
                        .foregroundStyle(.white)
                }
            }
        }
    }

    /// After a call: how it ended. With no call: only a failure, as the rest of what the model says belongs to a call.
    private var afterCall: String? {
        if model.status != model.words.ready { return model.status }
        return endedCall == nil ? nil : model.words.ended  // A new language puts the first words back.
    }

    /// A broken call says so here. Everything else about a call the agent says by voice.
    private var trouble: String? {
        if model.isLinkDown { return model.words.noLink }
        return [model.words.interrupted, model.words.refused].contains(model.status) ? model.status : nil
    }

    @ViewBuilder private var action: some View {
        if model.phase == .idle {
            RoundButton(title: model.words.call, symbol: "phone.fill", color: Palette.accent) { model.startCall() }
        } else {
            HStack(alignment: .top, spacing: 0) {
                SideButton(title: model.soundOutput, symbol: "speaker.wave.2")
                    .accessibilityHidden(true)
                    .overlay { SoundPicker(label: model.words.chooseSound, journal: model.journal) }
                    .frame(maxWidth: .infinity)
                RoundButton(title: model.words.hangUp, symbol: "phone.down.fill", color: Palette.red, symbolColor: .black) { model.endCall() }
                    .frame(maxWidth: .infinity)
                Button { model.toggleMute() } label: {
                    SideButton(title: model.isMuted ? model.words.muted : model.words.mute, symbol: model.isMuted ? "mic.slash" : "mic", isLit: model.isMuted)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(model.isMuted ? model.words.unmuteMicrophone : model.words.muteMicrophone)
                .frame(maxWidth: .infinity)
            }
        }
    }

    private var pages: some View {
        HStack {
            Button { page = .settings } label: { SlidersIcon().pageIcon() }
                .accessibilityLabel(model.words.settingsTitle)
            Spacer()
            Button { page = .questions } label: {
                Image(systemName: "questionmark.circle")
                    .font(.system(size: 25, weight: .light))
                    .pageIcon()
            }
            .accessibilityLabel(model.words.questionsTitle)
        }
        .padding(.top, 17)
        .padding(.horizontal, 20)
    }

    private static func clock(_ time: TimeInterval) -> String {
        let whole = Int(time)
        let minutes = String(format: "%d:%02d", whole / 60 % 60, whole % 60)
        return whole < 3600 ? minutes : String(format: "%d:%02d:%02d", whole / 3600, whole / 60 % 60, whole % 60)
    }
}

private struct SlidersIcon: View {
    var body: some View {
        Canvas { context, size in
            let scale = size.width / 24
            var lines = Path()
            for (from, to, y) in [(3.0, 14.0, 7.0), (19, 21, 7), (3, 5, 17), (10, 21, 17)] {
                lines.move(to: CGPoint(x: from, y: y))
                lines.addLine(to: CGPoint(x: to, y: y))
            }
            lines.addEllipse(in: CGRect(x: 14, y: 4.5, width: 5, height: 5))
            lines.addEllipse(in: CGRect(x: 5, y: 14.5, width: 5, height: 5))
            context.scaleBy(x: scale, y: scale)
            context.stroke(lines, with: .color(Palette.muted), style: StrokeStyle(lineWidth: 1.7, lineCap: .round))
        }
        .frame(width: 26, height: 26)
    }
}

private extension View {
    func pageIcon() -> some View {
        foregroundStyle(Palette.muted)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }
}

private struct Hosted: UIViewRepresentable {
    let view: UIView
    func makeUIView(context: Context) -> UIView { view }
    func updateUIView(_ uiView: UIView, context: Context) {}
}

/// The system's own list of where sound can go, on the same terms as the picker below.
private final class SoundPickerView: AVRoutePickerView {
    override func layoutSubviews() {
        super.layoutSubviews()
        subviews.forEach {
            $0.frame = bounds
            $0.accessibilityLabel = accessibilityLabel
        }
    }
}

private struct SoundPicker: UIViewRepresentable {
    let label: String
    let journal: SharedJournal?

    func makeCoordinator() -> Coordinator { Coordinator(journal: journal) }

    func makeUIView(context: Context) -> UIView {
        let picker = SoundPickerView()
        picker.alpha = 0.02
        picker.accessibilityLabel = label
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIView(_ uiView: UIView, context: Context) {}

    final class Coordinator: NSObject, AVRoutePickerViewDelegate {
        private let journal: SharedJournal?

        init(journal: SharedJournal?) { self.journal = journal }

        func routePickerViewWillBeginPresentingRoutes(_ routePickerView: AVRoutePickerView) { journal?.append("Список звука открывается") }
        func routePickerViewDidEndPresentingRoutes(_ routePickerView: AVRoutePickerView) { journal?.append("Список звука закрыт") }
    }
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
