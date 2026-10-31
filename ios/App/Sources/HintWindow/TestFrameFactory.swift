import UIKit

enum TestFrameFactory {
    static func makeScreenText(targetLang: String) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let rows = targetLang.lowercased().hasPrefix("zh")
            ? ["Search products", "搜索商品", "立即购买", "加入购物车"]
            : ["Cari produk", "Sampo anti ketombe", "Beli sekarang", "Tambah ke keranjang"]
        return UIGraphicsImageRenderer(size: CGSize(width: 720, height: 960), format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 720, height: 960))
            for (index, row) in rows.enumerated() {
                let y = CGFloat(100 + index * 190)
                UIColor.systemGray5.setFill()
                context.fill(CGRect(x: 24, y: y - 20, width: 672, height: 110))
                (row as NSString).draw(in: CGRect(x: 42, y: y, width: 636, height: 80), withAttributes: [
                    .font: UIFont.systemFont(ofSize: 42, weight: .medium), .foregroundColor: UIColor.black
                ])
            }
        }
    }

    static func make(number: Int? = nil, date: Date = Date()) -> UIImage {
        let size = CGSize(width: 720, height: 960)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            let background = number?.isMultiple(of: 2) == true
                ? UIColor(red: 0.04, green: 0.25, blue: 0.29, alpha: 1)
                : UIColor(red: 0.08, green: 0.12, blue: 0.23, alpha: 1)
            background.setFill()
            context.fill(CGRect(origin: .zero, size: size))

            let paragraph = NSMutableParagraphStyle()
            paragraph.alignment = .center
            func text(_ value: String, y: CGFloat, height: CGFloat, font: UIFont) {
                (value as NSString).draw(
                    in: CGRect(x: 30, y: y, width: 660, height: height),
                    withAttributes: [.font: font, .foregroundColor: UIColor.white, .paragraphStyle: paragraph]
                )
            }

            if let number {
                text("ТЕСТ ОКНА", y: 90, height: 80, font: .boldSystemFont(ofSize: 54))
                text("\(number)", y: 240, height: 250, font: .monospacedDigitSystemFont(ofSize: 200, weight: .bold))
                let formatter = DateFormatter()
                formatter.locale = Locale(identifier: "ru_RU")
                formatter.dateFormat = "HH:mm:ss"
                text(formatter.string(from: date), y: 540, height: 110, font: .monospacedDigitSystemFont(ofSize: 82, weight: .medium))
                text("Новый кадр\nкаждые 3 секунды", y: 740, height: 150, font: .systemFont(ofSize: 46))
            } else {
                text("Ожидание\nподсказки", y: 340, height: 240, font: .boldSystemFont(ofSize: 68))
            }
        }
    }
}
