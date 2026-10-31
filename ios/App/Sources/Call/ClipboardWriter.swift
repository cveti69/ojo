import Shared
import UIKit

@MainActor
enum ClipboardWriter {
    @discardableResult
    static func write(_ text: String, reason: String, journal: SharedJournal?) -> Bool {
        let state: String
        switch UIApplication.shared.applicationState {
        case .active: state = "активно"
        case .background: state = "в фоне"
        case .inactive: state = "неактивно"
        @unknown default: state = "неизвестно"
        }
        let pasteboard = UIPasteboard.general
        let previous = pasteboard.changeCount
        journal?.append("\(reason): попытка записи; приложение \(state)")
        pasteboard.string = text
        let success = pasteboard.string == text
        journal?.append("\(reason): приложение \(state); запись вернулась без ошибки; проверка \(success ? "успешна" : "НЕ ПРОШЛА"); changeCount \(previous) → \(pasteboard.changeCount)")
        return success
    }
}
