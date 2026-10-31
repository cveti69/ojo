import Shared
import UIKit

@MainActor
final class KeyboardViewController: UIInputViewController {
    private let explanation = UILabel()
    private let statusLabel = UILabel()
    private let chips = UIStackView()
    private let translateButton = UIButton(type: .system)
    private var pollingTask: Task<Void, Never>?
    private var translationTask: Task<Void, Never>?
    private var translationID = UUID()
    private var store: SharedStore?
    private var journal: SharedJournal?
    private var client: ServerClient?
    private var isVisible = false
    private var lastStepID: String?
    private var lastSessionID: String?
    private var lastTexts: [String] = []
    private var lastError: String?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .secondarySystemBackground
        explanation.font = .preferredFont(forTextStyle: .subheadline)
        explanation.numberOfLines = 2
        explanation.text = "Готовый текст для текущего шага"
        statusLabel.font = .preferredFont(forTextStyle: .footnote)
        statusLabel.numberOfLines = 3
        statusLabel.textColor = .secondaryLabel
        chips.axis = .horizontal
        chips.spacing = 8
        let scroll = UIScrollView()
        scroll.showsHorizontalScrollIndicator = false
        scroll.addSubview(chips)
        chips.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            chips.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            chips.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            chips.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
            chips.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
            chips.heightAnchor.constraint(equalTo: scroll.frameLayoutGuide.heightAnchor),
            scroll.heightAnchor.constraint(equalToConstant: 48)
        ])
        translateButton.setTitle("Перевести", for: .normal)
        translateButton.addTarget(self, action: #selector(translate), for: .touchUpInside)
        let globe = UIButton(type: .system)
        globe.setImage(UIImage(systemName: "globe"), for: .normal)
        globe.accessibilityLabel = "Следующая клавиатура"
        globe.addTarget(self, action: #selector(advanceToNextInputMode), for: .touchUpInside)
        let delete = UIButton(type: .system)
        delete.setImage(UIImage(systemName: "delete.left"), for: .normal)
        delete.accessibilityLabel = "Стереть символ"
        delete.addTarget(self, action: #selector(deleteCharacter), for: .touchUpInside)
        let controls = UIStackView(arrangedSubviews: [globe, translateButton, delete])
        controls.axis = .horizontal
        controls.distribution = .equalSpacing
        controls.heightAnchor.constraint(equalToConstant: 44).isActive = true
        let stack = UIStackView(arrangedSubviews: [explanation, scroll, statusLabel, controls])
        stack.axis = .vertical
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -12),
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 12),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: view.bottomAnchor, constant: -8),
            view.heightAnchor.constraint(greaterThanOrEqualToConstant: 210)
        ])
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        isVisible = true
        startPolling()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        isVisible = false
        pollingTask?.cancel()
        pollingTask = nil
        translationTask?.cancel()
        translationTask = nil
        translationID = UUID()
        journal?.append("Клавиатура скрыта; опрос остановлен")
    }

    private func startPolling() {
        pollingTask?.cancel()
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.isVisible else { break }
                await self.refreshSuggestions()
                do { try await Task.sleep(nanoseconds: 1_000_000_000) } catch { break }
            }
        }
    }

    private func prepareStore() throws -> SharedStore {
        if let store { return store }
        let configuration = try ProjectConfiguration()
        let store = try SharedStore(configuration: configuration)
        self.store = store
        journal = SharedJournal(configuration: configuration, source: "Клавиатура")
        journal?.append("Клавиатура показана; полный доступ включён")
        return store
    }

    private func refreshSuggestions() async {
        guard hasFullAccess else {
            clearChips()
            translateButton.isEnabled = false
            statusLabel.text = "Включите «Разрешить полный доступ»: Настройки → Основные → Клавиатура → Клавиатуры → эта клавиатура."
            return
        }
        translateButton.isEnabled = translationTask == nil
        do {
            let store = try prepareStore()
            guard let sessionID = store.sessionID else {
                clearChips()
                statusLabel.text = "Откройте приложение и нажмите «Позвонить», чтобы получить готовый текст."
                return
            }
            if sessionID != lastSessionID {
                clearChips()
                lastSessionID = sessionID
            }
            let response = try await networkClient(settings: store.settings).suggestions(sessionID: sessionID)
            try Task.checkCancellation()
            guard isVisible, hasFullAccess, store.sessionID == sessionID else { return }
            if lastStepID != response.stepID || lastTexts != Array(response.texts.prefix(3)) {
                showChips(Array(response.texts.prefix(3)))
                lastStepID = response.stepID
            }
            explanation.text = response.hintRU.isEmpty ? "Готовый текст для текущего шага" : response.hintRU
            statusLabel.text = response.texts.isEmpty ? "Ждём текст от агента…" : "Нажмите на текст, чтобы вставить его."
            lastError = nil
        } catch {
            guard !Task.isCancelled else { return }
            statusLabel.text = error.localizedDescription
            if lastError != error.localizedDescription {
                lastError = error.localizedDescription
                journal?.append("Ошибка /suggestions: \(error.localizedDescription)")
            }
            clearChips()
        }
    }

    private func showChips(_ texts: [String]) {
        clearChips()
        lastTexts = texts
        for text in texts {
            let button = UIButton(type: .system)
            var configuration = UIButton.Configuration.tinted()
            configuration.title = text
            configuration.cornerStyle = .capsule
            button.configuration = configuration
            button.addAction(UIAction { [weak self] _ in
                guard let self else { return }
                self.textDocumentProxy.insertText(text)
                self.journal?.append("Текст из чипа вставлен")
            }, for: .touchUpInside)
            chips.addArrangedSubview(button)
        }
    }

    private func networkClient(settings: ServerSettings) -> ServerClient {
        if let client, client.settings == settings { return client }
        let client = ServerClient(settings: settings)
        self.client = client
        return client
    }

    private func clearChips() {
        for view in chips.arrangedSubviews { view.removeFromSuperview() }
        lastTexts = []
        lastStepID = nil
        explanation.text = "Готовый текст для текущего шага"
    }

    @objc private func deleteCharacter() { textDocumentProxy.deleteBackward() }

    @objc private func translate() {
        guard hasFullAccess, translationTask == nil else { return }
        guard let original = textDocumentProxy.documentContextBeforeInput, !original.isEmpty else {
            statusLabel.text = "Сначала наберите текст обычной клавиатурой, затем вернитесь сюда."
            return
        }
        let documentID = textDocumentProxy.documentIdentifier
        let after = textDocumentProxy.documentContextAfterInput
        translateButton.isEnabled = false
        statusLabel.text = "Переводим…"
        let identifier = UUID()
        translationID = identifier
        translationTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.translationID == identifier {
                    self.translationTask = nil
                    self.translateButton.isEnabled = self.hasFullAccess
                }
            }
            do {
                let store = try self.prepareStore()
                guard let sessionID = store.sessionID else { throw ClientError.http(404) }
                let result = try await self.networkClient(settings: store.settings).translate(original, sessionID: sessionID)
                try Task.checkCancellation()
                guard self.isVisible, self.hasFullAccess, store.sessionID == sessionID,
                      self.textDocumentProxy.documentIdentifier == documentID,
                      self.textDocumentProxy.documentContextBeforeInput == original,
                      self.textDocumentProxy.documentContextAfterInput == after else {
                    self.statusLabel.text = "Поле изменилось. Нажмите «Перевести» ещё раз."
                    return
                }
                guard !result.text.isEmpty else { throw ClientError.invalidResponse }
                for _ in original { self.textDocumentProxy.deleteBackward() }
                self.textDocumentProxy.insertText(result.text)
                self.statusLabel.text = "Перевод вставлен"
                self.journal?.append("Перевод вставлен; язык из App Group: \(store.settings.targetLang)")
            } catch {
                guard !Task.isCancelled else { return }
                self.statusLabel.text = error.localizedDescription
                self.journal?.append("Ошибка /translate: \(error.localizedDescription)")
            }
        }
    }

    deinit {
        pollingTask?.cancel()
        translationTask?.cancel()
    }
}
