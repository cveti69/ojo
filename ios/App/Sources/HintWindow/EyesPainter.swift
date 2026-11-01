import CoreGraphics

/// Draws the eyes from numbers, so they can look anywhere. Sizes follow the box: the floating window is small, and anything
/// thinner than 24 pixels out of 720 is lost in it. The context has its origin at the top left.
enum EyesPainter {
    private static let eyeWidth = 0.42  // Of the box.
    private static let eyeHeight = 1.55  // Of the eye's width.
    private static let pupilWidth = 0.5  // Of the eye's width.
    private static let thinnest: CGFloat = 12
    private static let smallestShine: CGFloat = 14
    private static let iconBox: CGFloat = 720  // The eyes and the nose share this box on the icon.
    private static let iconMiddle = CGPoint(x: 512, y: 400)

    static func draw(_ pose: EyePose, in box: CGRect, on context: CGContext) {
        let width = box.width * eyeWidth
        let height = width * eyeHeight
        let left = CGPoint(x: box.minX + width / 2, y: box.midY)
        let right = CGPoint(x: box.maxX - width / 2, y: box.midY)
        let thick = max(box.width * 0.056, thinnest)
        context.setLineCap(.round)
        context.setStrokeColor(red: 1, green: 1, blue: 1, alpha: 1)
        if pose.isHappy {
            context.setLineWidth(thick)
            for center in [left, right] {
                context.addArc(center: CGPoint(x: center.x, y: center.y + width * 0.2), radius: width * 0.46,
                               startAngle: .pi * 1.12, endAngle: .pi * 1.88, clockwise: false)
                context.strokePath()
            }
            return
        }
        eye(at: left, width: width, height: height, open: pose.left, inward: 1, pose: pose, on: context)
        eye(at: right, width: width, height: height, open: pose.right, inward: -1, pose: pose, on: context)
        if pose.brow > 0 {
            let top = right.y - height / 2 - thick * (0.9 + 1.3 * CGFloat(pose.browLift))
            context.setStrokeColor(red: 1, green: 1, blue: 1, alpha: CGFloat(pose.brow))
            context.setLineWidth(thick)
            context.move(to: CGPoint(x: right.x - width * 0.46, y: top + thick * 0.55))
            context.addLine(to: CGPoint(x: right.x + width * 0.46, y: top - thick * 0.55))
            context.strokePath()
        }
    }

    /// The nose of the app's icon, at half its size there, in the very colour of the window's border. It is drawn below the eyes.
    static func drawNose(in box: CGRect, on context: CGContext) {
        let scale = box.width / iconBox
        context.saveGState()
        context.translateBy(x: box.midX, y: box.midY)
        context.scaleBy(x: scale, y: scale)
        context.translateBy(x: -iconMiddle.x, y: -iconMiddle.y)
        context.move(to: CGPoint(x: 477, y: 620))
        context.addArc(center: CGPoint(x: 512, y: 620), radius: 35, startAngle: .pi, endAngle: 0, clockwise: false)
        context.addLine(to: CGPoint(x: 547, y: 744))
        context.addArc(center: CGPoint(x: 450, y: 744), radius: 97, startAngle: 0, endAngle: .pi * 5 / 6, clockwise: false)
        context.addArc(center: CGPoint(x: 396.31, y: 775), radius: 35, startAngle: .pi * 5 / 6, endAngle: .pi * 11 / 6, clockwise: false)
        context.addArc(center: CGPoint(x: 450, y: 744), radius: 27, startAngle: .pi * 5 / 6, endAngle: 0, clockwise: true)
        context.closePath()
        context.setFillColor(red: 150.0 / 255, green: 40.0 / 255, blue: 245.0 / 255, alpha: 1)
        context.fillPath()
        context.restoreGState()
    }

    private static func eye(at center: CGPoint, width: CGFloat, height: CGFloat, open: Double, inward: Double,
                            pose: EyePose, on context: CGContext) {
        let tall = max(height * CGFloat(open), width * 0.14)
        let shape = CGRect(x: center.x - width / 2, y: center.y - tall / 2, width: width, height: tall)
        let lid = tall * CGFloat(pose.squint) * 0.45
        context.saveGState()
        context.addEllipse(in: shape)
        context.clip()
        context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
        context.fill(CGRect(x: shape.minX, y: shape.minY + lid, width: width, height: tall - lid))
        guard tall >= width * 0.5 else {  // An eye this shut shows no pupil.
            context.restoreGState()
            return
        }
        let side = min(width * pupilWidth * CGFloat(pose.pupil), width * 0.8)
        let x = center.x + (width - side) / 2 * 0.9 * CGFloat(max(-1, min(1, pose.x + inward * pose.cross)))
        let y = center.y + max(0, tall - side) / 2 * 0.9 * CGFloat(max(-1, min(1, pose.y)))
        context.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
        context.fillEllipse(in: CGRect(x: x - side / 2, y: y - side / 2, width: side, height: side))
        let shine = side * 0.3
        if shine >= smallestShine {
            context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
            context.fillEllipse(in: CGRect(x: x + side * 0.08, y: y - side * 0.34, width: shine, height: shine))
        }
        context.restoreGState()
    }
}
