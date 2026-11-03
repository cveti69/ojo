import UIKit

/// A plain card for the window when the eyes are not there to fill it.
enum TestFrameFactory {
    static func make(title: String) -> UIImage {
        let size = CGSize(width: 720, height: 960)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor(red: 0.08, green: 0.12, blue: 0.23, alpha: 1).setFill()
            context.fill(CGRect(origin: .zero, size: size))
            let paragraph = NSMutableParagraphStyle()
            paragraph.alignment = .center
            (title as NSString).draw(in: CGRect(x: 30, y: 340, width: 660, height: 240), withAttributes: [
                .font: UIFont.boldSystemFont(ofSize: 68), .foregroundColor: UIColor.white, .paragraphStyle: paragraph
            ])
        }
    }
}
