import Shared
import SwiftUI

/// What the eyes mean, and the answers a person wants before showing a screen.
struct QuestionsScreen: View {
    let words: CallWords
    let back: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: words.questionsTitle, backLabel: words.back, back: back)
            ScrollView {
                VStack(alignment: .leading, spacing: 36) {
                    HStack(alignment: .top, spacing: 8) {
                        meaning(.reading, words.looking)
                        meaning(.thinking, words.thinking)
                        meaning(.shut, words.notLooking)
                    }
                    VStack(alignment: .leading, spacing: 26) {
                        ForEach(words.questions, id: \.title) { question in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(question.title)
                                    .font(.system(.headline, design: .rounded))
                                    .foregroundStyle(.white)
                                    .accessibilityAddTraits(.isHeader)
                                Text(question.answer)
                                    .font(.system(.subheadline, design: .rounded))
                                    .lineSpacing(3)
                                    .foregroundStyle(Palette.muted)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .padding(.top, 28)
                .padding(.bottom, 34)
            }
            .scrollIndicators(.hidden)
        }
        .padding(.top, 17)
        .padding(.horizontal, 20)
        .backOnEdgeSwipe(back)
    }

    private func meaning(_ pose: EyePose, _ title: String) -> some View {
        VStack(spacing: 10) {
            Face(pose: pose, blinks: false)
                .frame(width: 84, height: 80)
                .accessibilityHidden(true)
            Text(title)
                .font(.system(.footnote, design: .rounded, weight: .medium))
                .foregroundStyle(Palette.muted)
                .lineLimit(1)
                .minimumScaleFactor(0.4)
        }
        .frame(maxWidth: .infinity)
    }
}
