import SwiftUI
import UIKit

/// UITextDocumentProxy を TextTarget として扱うアダプタ
@MainActor
final class ProxyTextTarget: TextTarget {
    weak var controller: UIInputViewController?

    init(controller: UIInputViewController) {
        self.controller = controller
    }

    private var proxy: UITextDocumentProxy? { controller?.textDocumentProxy }

    var textBeforeCursor: String { proxy?.documentContextBeforeInput ?? "" }
    var textAfterCursor: String { proxy?.documentContextAfterInput ?? "" }
    var selectedText: String? { proxy?.selectedText }

    func insert(_ text: String) { proxy?.insertText(text) }
    func deleteBackward() { proxy?.deleteBackward() }
    func moveCursor(by offset: Int) { proxy?.adjustTextPosition(byCharacterOffset: offset) }
}

/// キーボード拡張のエントリポイント。SwiftUI の KeyboardView をホストする
final class KeyboardViewController: UIInputViewController {
    private var model: KeyboardModel!
    private var host: UIHostingController<KeyboardView>!
    private var heightConstraint: NSLayoutConstraint?

    override func viewDidLoad() {
        super.viewDidLoad()
        let store = AppStore.shared
        store.reload()
        store.state.keyboardEverLoaded = true
        store.state.keyboardFullAccess = hasFullAccess

        model = KeyboardModel(store: store, target: ProxyTextTarget(controller: self))
        model.hasFullAccess = hasFullAccess
        model.onGlobe = { [weak self] in self?.advanceToNextInputMode() }
        model.onOpenApp = { [weak self] url in self?.openContainingApp(url) }

        host = UIHostingController(rootView: KeyboardView(model: model))
        host.view.backgroundColor = .clear
        host.sizingOptions = []
        addChild(host)
        view.addSubview(host.view)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            host.view.topAnchor.constraint(equalTo: view.topAnchor),
            host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        host.didMove(toParent: self)

        let height = view.heightAnchor.constraint(equalToConstant: KA.K.totalHeight)
        height.priority = .defaultHigh
        height.isActive = true
        heightConstraint = height
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        AppStore.shared.reload()
        model.needsGlobe = needsInputModeSwitchKey
        model.hasFullAccess = hasFullAccess
        model.mode = .typing
        model.updateAutoShift()
        applyAppearance()
    }

    override func textDidChange(_ textInput: UITextInput?) {
        super.textDidChange(textInput)
        model?.updateAutoShift()
        applyAppearance()
    }

    private func applyAppearance() {
        let style: UIUserInterfaceStyle
        switch AppStore.shared.state.appearance {
        case .automatic: style = textDocumentProxy.keyboardAppearance == .dark ? .dark : .unspecified
        case .light: style = .light
        case .dark: style = .dark
        }
        host?.overrideUserInterfaceStyle = style
    }

    /// 拡張から本体アプリを開く。キーボード拡張には公開 API が無いため、レスポンダーチェーンを辿る一般的な方法を使う
    /// (iOS のバージョンによっては開けない。README の「既知の制限」参照)
    private func openContainingApp(_ url: URL) {
        var responder: UIResponder? = self
        let selector = Selector(("openURL:"))
        while let r = responder {
            if r.responds(to: selector), r !== self {
                _ = r.perform(selector, with: url)
                return
            }
            responder = r.next
        }
    }
}
