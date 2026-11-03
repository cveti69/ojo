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
                    CheckRow(title: words.fastMode, isOn: model.fastMode) { model.setFastMode(!model.fastMode) }
                    CheckRow(title: words.nextStepTest, isOn: model.nextStepTest) { model.setNextStepTest(!model.nextStepTest) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 28)
                .padding(.bottom, 20)
            }
            .scrollIndicators(.hidden)
            .scrollDismissesKeyboard(.interactively)
            HStack(spacing: 0) {
                Link(destination: URL(string: "https://callojo.app/privacy/")!) { footer(words.yourData) }
                Text("·").foregroundStyle(Palette.muted).accessibilityHidden(true)
                Button {
                    UIPasteboard.general.string = model.journal?.contents() ?? ""
                    copied = true
                } label: {
                    footer(copied ? words.journalCopied : words.copyJournal)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 17)
        .padding(.horizontal, 20)
        .padding(.bottom, 14)
        .backOnEdgeSwipe(back)
        .task { await model.loadVoices() }
    }

    private func footer(_ text: String) -> some View {
        Text(text)
            .font(.system(.subheadline, design: .rounded, weight: .medium))
            .foregroundStyle(Palette.muted)
            .padding(.horizontal, 16)
            .frame(minHeight: 44)
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

extension View {
    /// A swipe from the left edge goes back, as it does in the system's own screens. A thin strip along the edge takes it,
    /// so the scroll under it never swallows the swipe; it is narrower than the margin, so it covers nothing to tap.
    func backOnEdgeSwipe(_ back: @escaping () -> Void) -> some View {
        overlay(alignment: .leading) {
            Color.clear
                .frame(width: 16)
                .contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 10, coordinateSpace: .global).onEnded { drag in
                    if drag.translation.width > 60, abs(drag.translation.height) < drag.translation.width { back() }
                })
                .accessibilityHidden(true)
        }
    }
}
