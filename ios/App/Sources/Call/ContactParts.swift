import SwiftUI

/// The colours of the app's own screens, black in any system setting.
enum Palette {
    static let accent = Color(red: 150.0 / 255, green: 40.0 / 255, blue: 245.0 / 255)  // The window's border has this colour too.
    static let red = Color(red: 1, green: 69.0 / 255, blue: 58.0 / 255)
    static let muted = Color(red: 161.0 / 255, green: 161.0 / 255, blue: 170.0 / 255)
    static let label = Color(red: 209.0 / 255, green: 209.0 / 255, blue: 214.0 / 255)
    static let line = Color(red: 58.0 / 255, green: 58.0 / 255, blue: 60.0 / 255)
    static let control = Color(red: 44.0 / 255, green: 44.0 / 255, blue: 46.0 / 255)
}

extension EyePose {
    static let atTheButton = EyePose(y: 0.63, cross: 0.176)
    static let reading = EyePose(y: 0.42)
    static let thinking = EyePose(x: -0.88, y: -0.63)
    static let shut = EyePose(left: 0.111, right: 0.111)
}

/// The face of the icon on the black of the screen, drawn by the code that draws the eyes in the window.
struct Face: View {
    var pose = EyePose()
    var blinks = true
    @State private var lid = 1.0
    @Environment(\.accessibilityReduceMotion) private var isStill
    @Environment(\.scenePhase) private var scenePhase

    private static let picture = CGSize(width: 744, height: 710)
    private static let box = CGRect(x: 12, y: -110, width: 720, height: 720)  // The icon's box, where it stands in the picture.

    private var isAlive: Bool { blinks && !isStill && scenePhase == .active }

    var body: some View {
        Canvas { context, size in
            context.withCGContext { drawing in
                drawing.scaleBy(x: size.width / Self.picture.width, y: size.height / Self.picture.height)
                var shown = pose
                shown.left *= lid
                shown.right *= lid
                EyesPainter.draw(shown, in: Self.box, on: drawing)
                EyesPainter.drawNose(in: Self.box, on: drawing)
            }
        }
        .task(id: isAlive) {
            lid = 1
            if isAlive { await blink() }
        }
    }

    /// The blinks of the eyes in the window at rest: every 5 to 9 seconds, at times twice.
    private func blink() async {
        var eyes = EyesDirector()
        eyes.isCalm = true
        let start = Date()
        while !Task.isCancelled {
            lid = eyes.advance(to: Date().timeIntervalSince(start)).left
            let wait = max(eyes.nextChange - Date().timeIntervalSince(start), EyesDirector.frame)
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
        }
        lid = 1
    }
}

/// The big round button: one place on every screen with the face.
struct RoundButton: View {
    let title: String
    let symbol: String
    let color: Color
    var symbolColor = Color.white
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 10) {
                Image(systemName: symbol)
                    .font(.system(size: 30))
                    .foregroundStyle(symbolColor)
                    .frame(width: 80, height: 80)
                    .background(color, in: Circle())
                Text(title)
                    .font(.system(.subheadline, design: .rounded, weight: .medium))
                    .foregroundStyle(Palette.label)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .padding(.horizontal, 4)
            }
        }
        .buttonStyle(.plain)
    }
}

/// A smaller round button beside the big one, as tall as it, so the three captions stand in a row.
struct SideButton: View {
    let title: String
    let symbol: String
    var isLit = false

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 24))
                .foregroundStyle(isLit ? .black : .white)
                .frame(width: 64, height: 64)
                .background(isLit ? .white : Palette.control, in: Circle())
                .frame(width: 80, height: 80)
            Text(title)
                .font(.system(.subheadline, design: .rounded, weight: .medium))
                .foregroundStyle(Palette.label)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .padding(.horizontal, 4)
        }
    }
}

/// The top of a page: its title, and the way back.
struct PageHeader: View {
    let title: String
    let backLabel: String
    let back: () -> Void

    var body: some View {
        ZStack {
            Text(title)
                .font(.system(.headline, design: .rounded))
                .foregroundStyle(.white)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .padding(.horizontal, 44)
            HStack {
                Button(action: back) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 22, weight: .light))
                        .foregroundStyle(Palette.muted)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel(backLabel)
                Spacer()
            }
        }
        .frame(minHeight: 44)
    }
}

struct Pill: View {
    let title: String
    let isOn: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(.subheadline, design: .rounded, weight: .medium))
                .foregroundStyle(.white)
                .padding(.horizontal, 14)
                .frame(minHeight: 44)
                .background(isOn ? Palette.accent : .clear, in: Capsule())
                .overlay { Capsule().strokeBorder(isOn ? Palette.accent : Palette.line, lineWidth: 1.5) }
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

/// One thing that is on or off, so it stands alone and not among pills.
struct CheckRow: View {
    let title: String
    let isOn: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: "checkmark")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.white)
                    .opacity(isOn ? 1 : 0)
                    .frame(width: 26, height: 26)
                    .background(isOn ? Palette.accent : .clear, in: RoundedRectangle(cornerRadius: 7))
                    .overlay { RoundedRectangle(cornerRadius: 7).strokeBorder(isOn ? Palette.accent : Palette.line, lineWidth: 1.5) }
                Text(title)
                    .font(.system(.subheadline, design: .rounded, weight: .medium))
                    .foregroundStyle(.white)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

/// Puts what it holds in a row, and what does not fit on the next one.
struct Rows: Layout {
    private static let gap: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrange(subviews, width: proposal.width ?? .infinity).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let places = arrange(subviews, width: bounds.width).places
        for (view, place) in zip(subviews, places) {
            view.place(at: CGPoint(x: bounds.minX + place.x, y: bounds.minY + place.y), proposal: ProposedViewSize(width: bounds.width, height: nil))
        }
    }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> (places: [CGPoint], size: CGSize) {
        var places: [CGPoint] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var tallest: CGFloat = 0
        var widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(ProposedViewSize(width: width, height: nil))
            if x > 0, x + size.width > width {
                x = 0
                y += tallest + Self.gap
                tallest = 0
            }
            places.append(CGPoint(x: x, y: y))
            widest = max(widest, x + size.width)
            x += size.width + Self.gap
            tallest = max(tallest, size.height)
        }
        return (places, CGSize(width: widest, height: y + tallest))
    }
}
