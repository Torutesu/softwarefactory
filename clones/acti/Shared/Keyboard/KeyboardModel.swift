import AudioToolbox
import SwiftUI
import UIKit

// キーボードの状態と入力ロジック。キーボード拡張 (実際の入力欄) とアプリ内デモ (擬似チャット) の両方で使う。

/// 入力先の抽象。拡張では UITextDocumentProxy、デモでは String のバインディングをラップする
@MainActor
protocol TextTarget: AnyObject {
    var textBeforeCursor: String { get }
    var textAfterCursor: String { get }
    var selectedText: String? { get }
    func insert(_ text: String)
    func deleteBackward()
    func moveCursor(by offset: Int)
}

extension TextTarget {
    var fullText: String { textBeforeCursor + (selectedText ?? "") + textAfterCursor }

    /// Act の入力にする文章: 選択範囲があればそれ、無ければ入力欄全体
    var actInputText: String {
        if let s = selectedText, !s.isEmpty { return s }
        return fullText
    }

    func replaceAll(with text: String) {
        if !textAfterCursor.isEmpty { moveCursor(by: textAfterCursor.count) }
        if let s = selectedText, !s.isEmpty { deleteBackward() }
        for _ in 0..<textBeforeCursor.count { deleteBackward() }
        insert(text)
    }

    func append(_ text: String) {
        if !textAfterCursor.isEmpty { moveCursor(by: textAfterCursor.count) }
        let needsSpace = !(textBeforeCursor.last?.isWhitespace ?? true)
        insert((needsSpace ? "\n" : "") + text)
    }
}

/// アプリ内デモ用: 文字列を直接持つ入力先 (カーソルは常に末尾)
@MainActor
final class StringTextTarget: TextTarget {
    var get: () -> String
    var set: (String) -> Void
    var selectAllOnNextEdit = false

    init(get: @escaping () -> String, set: @escaping (String) -> Void) {
        self.get = get
        self.set = set
    }

    var textBeforeCursor: String { selectAllOnNextEdit ? "" : get() }
    var textAfterCursor: String { "" }
    var selectedText: String? { selectAllOnNextEdit ? get() : nil }

    func insert(_ text: String) {
        if selectAllOnNextEdit { selectAllOnNextEdit = false; set(text) } else { set(get() + text) }
    }

    func deleteBackward() {
        if selectAllOnNextEdit { selectAllOnNextEdit = false; set(""); return }
        var s = get()
        if !s.isEmpty { s.removeLast() }
        set(s)
    }

    func moveCursor(by offset: Int) {}
}

enum KeyboardLayer { case letters, numbers, symbols }
enum ShiftState { case off, on, locked }

enum KeyboardMode: Equatable {
    case typing
    case wheel
    case panel
    case acts
    case voice
    case clips
}

enum PanelState: Equatable {
    case thinking
    case result(String)
    case noCredits
    case error(String)
}

/// デモ中に出すコーチマーク
enum DemoHint: Equatable {
    case holdSpace
    case runActsHere
}

@MainActor
@Observable
final class KeyboardModel {
    // 依存
    @ObservationIgnored let store: AppStore
    @ObservationIgnored var target: TextTarget
    @ObservationIgnored var ai: AIService = MockAI()
    /// 地球儀キーが必要なとき (Face ID 機種以外) に拡張側が設定する
    @ObservationIgnored var onGlobe: (() -> Void)?
    @ObservationIgnored var needsGlobe = false
    @ObservationIgnored var hasFullAccess = true
    /// 結果を入力欄に反映した後に呼ばれる (デモの進行に使う)
    @ObservationIgnored var onActApplied: ((ActItem, String) -> Void)?
    /// キーボードのアプリ側で開くべき URL (拡張から本体を開く)
    @ObservationIgnored var onOpenApp: ((URL) -> Void)?
    let isDemo: Bool

    // 状態
    var layer: KeyboardLayer = .letters
    var shift: ShiftState = .on
    var mode: KeyboardMode = .typing
    var panel: PanelState = .thinking
    var activeAct: ActItem?
    var wheelSelection: Int?
    var wheelDrag: CGPoint?
    var hint: DemoHint?
    var pressedKey: String?
    var voiceListening = false
    var voiceTranslate: String?
    var voiceEmoji = false
    var lastResultInput = ""

    // 幾何 (ビューが設定)
    @ObservationIgnored var keysSize: CGSize = .zero

    // スペースバーのジェスチャー状態
    @ObservationIgnored private var spacePressing = false
    @ObservationIgnored private var spaceStart: CGPoint = .zero
    @ObservationIgnored private var spaceLastX: CGFloat = 0
    @ObservationIgnored private var cursorMode = false
    @ObservationIgnored private var longPressTask: Task<Void, Never>?
    @ObservationIgnored private var deleteTask: Task<Void, Never>?
    @ObservationIgnored private var runTask: Task<Void, Never>?
    @ObservationIgnored private var lastSpaceTap: Date?

    init(store: AppStore, target: TextTarget, isDemo: Bool = false) {
        self.store = store
        self.target = target
        self.isDemo = isDemo
        updateAutoShift()
    }

    var prefs: KeyboardPrefs { store.state.keyboard }

    // MARK: Keys

    func tapCharacter(_ raw: String) {
        let char = (layer == .letters && shift != .off) ? raw.uppercased() : raw
        target.insert(char)
        feedback()
        if shift == .on { shift = .off }
        if layer != .letters, raw == "'" || raw == " " { layer = .letters }
        updateAutoShift()
    }

    /// アルファベットキーの長押し: そのキーに割り当てた Act を実行
    func longPressCharacter(_ raw: String) -> Bool {
        guard let id = store.state.keyAssignments[raw.lowercased()], let act = store.act(id) else { return false }
        run(act)
        return true
    }

    func tapSpace() {
        feedback()
        let now = Date()
        if prefs.periodShortcut, let last = lastSpaceTap, now.timeIntervalSince(last) < 0.35,
           target.textBeforeCursor.hasSuffix(" "),
           let before = target.textBeforeCursor.dropLast().last, before.isLetter || before.isNumber {
            target.deleteBackward()
            target.insert(". ")
            lastSpaceTap = nil
        } else {
            target.insert(" ")
            lastSpaceTap = now
        }
        if layer != .letters { layer = .letters }
        updateAutoShift()
    }

    func tapReturn() {
        feedback()
        target.insert("\n")
        updateAutoShift()
    }

    func tapShift() {
        feedback()
        switch shift {
        case .off: shift = .on
        case .on: shift = .off
        case .locked: shift = .off
        }
    }

    func doubleTapShift() {
        shift = .locked
    }

    func tapLayerSwitch() {
        feedback()
        layer = layer == .letters ? .numbers : .letters
    }

    func tapSymbolsSwitch() {
        feedback()
        layer = layer == .symbols ? .numbers : .symbols
    }

    func deletePressed() {
        feedback()
        target.deleteBackward()
        deleteTask?.cancel()
        deleteTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(450))
            var interval = 100
            var count = 0
            while !Task.isCancelled {
                guard let self else { return }
                // 長押しが続くと単語単位で消す (Backspace Swipe Acceleration)
                if self.prefs.backspaceAcceleration, count > 12 {
                    self.deleteWord()
                } else {
                    self.target.deleteBackward()
                }
                count += 1
                if count > 6 { interval = 60 }
                try? await Task.sleep(for: .milliseconds(interval))
            }
        }
    }

    func deleteReleased() {
        deleteTask?.cancel()
        deleteTask = nil
        updateAutoShift()
    }

    private func deleteWord() {
        let before = target.textBeforeCursor
        guard !before.isEmpty else { return }
        var n = 0
        var seenNonSpace = false
        for ch in before.reversed() {
            if ch.isWhitespace && seenNonSpace { break }
            if !ch.isWhitespace { seenNonSpace = true }
            n += 1
        }
        for _ in 0..<n { target.deleteBackward() }
    }

    func updateAutoShift() {
        guard prefs.autoCapitalization, shift != .locked, layer == .letters else { return }
        let before = target.textBeforeCursor
        let trimmed = before.trimmingCharacters(in: .whitespaces)
        if before.isEmpty || before.hasSuffix("\n") {
            shift = .on
        } else if before.hasSuffix(" "), let last = trimmed.last, ".!?".contains(last) {
            shift = .on
        } else if shift == .on, !before.hasSuffix(" ") {
            shift = .off
        }
    }

    private func feedback() {
        if prefs.haptics {
            let style: UIImpactFeedbackGenerator.FeedbackStyle = [.light, .medium, .heavy][min(prefs.hapticStrength, 2)]
            UIImpactFeedbackGenerator(style: style).impactOccurred()
        }
        if prefs.keySounds, !isDemo, hasFullAccess {
            AudioServicesPlaySystemSound(1104)   // キークリック音 (フルアクセス時のみ鳴る)
        }
    }

    // MARK: Space bar (tap / cursor swipe / long-press wheel)

    func spaceChanged(_ value: DragGesture.Value) {
        if !spacePressing {
            spacePressing = true
            spaceStart = value.startLocation
            spaceLastX = value.startLocation.x
            cursorMode = false
            longPressTask?.cancel()
            let start = value.startLocation
            longPressTask = Task { [weak self] in
                try? await Task.sleep(for: .milliseconds(350))
                guard !Task.isCancelled, let self, self.spacePressing, !self.cursorMode else { return }
                self.openWheel(at: start)
            }
        }
        if mode == .wheel {
            wheelDrag = value.location
            let newSelection = wheelSlot(near: value.location)
            if newSelection != wheelSelection {
                wheelSelection = newSelection
                if newSelection != nil { UISelectionFeedbackGenerator().selectionChanged() }
            }
            return
        }
        let dx = value.location.x - spaceStart.x
        if prefs.spaceCursorControl, !cursorMode, abs(dx) > 12 {
            cursorMode = true
            longPressTask?.cancel()
        }
        if cursorMode {
            let step: CGFloat = 9
            let delta = value.location.x - spaceLastX
            let steps = Int(delta / step)
            if steps != 0 {
                target.moveCursor(by: steps)
                spaceLastX += CGFloat(steps) * step
            }
        }
    }

    func spaceEnded(_ value: DragGesture.Value) {
        spacePressing = false
        longPressTask?.cancel()
        if mode == .wheel {
            let selection = wheelSelection
            wheelSelection = nil
            wheelDrag = nil
            if let selection, let item = wheelItem(at: selection) {
                runWheelItem(item)
            } else {
                withAnimation(KA.M.wheelOpen) { mode = .typing }
            }
        } else if !cursorMode {
            tapSpace()
        }
        cursorMode = false
    }

    private func openWheel(at point: CGPoint) {
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        if hint == .holdSpace { hint = nil }
        withAnimation(KA.M.wheelOpen) {
            mode = .wheel
            wheelDrag = point
        }
    }

    // MARK: Wheel geometry

    /// スロットの中心座標 (キー領域の座標系)。左から 0...4、中央 (2) が Shogun AI
    func wheelSlotPoints(in size: CGSize) -> [CGPoint] {
        let center = CGPoint(x: size.width / 2, y: size.height - 14)
        let radius = min(size.width * 0.36, size.height * 0.78)
        let angles: [Double] = [166, 128, 90, 52, 14]
        return angles.map { deg in
            let r = deg * .pi / 180
            return CGPoint(x: center.x + radius * cos(r), y: center.y - radius * sin(r))
        }
    }

    func wheelSlot(near point: CGPoint) -> Int? {
        let pts = wheelSlotPoints(in: keysSize)
        var best: (Int, CGFloat)?
        for (i, p) in pts.enumerated() {
            let d = hypot(p.x - point.x, p.y - point.y)
            if d < 56, best == nil || d < best!.1 { best = (i, d) }
        }
        guard let index = best?.0, wheelItem(at: index) != nil else { return nil }
        return index
    }

    enum WheelItem: Equatable {
        case act(ActItem)
        case tool(String)   // "clipboard" / "voice"
    }

    func wheelItem(at index: Int) -> WheelItem? {
        guard index < store.state.wheelSlots.count, let id = store.state.wheelSlots[index] else { return nil }
        if id == "tool.clipboard" { return .tool("clipboard") }
        if id == "tool.voice" { return .tool("voice") }
        if let act = store.act(id) { return .act(act) }
        return nil
    }

    private func runWheelItem(_ item: WheelItem) {
        switch item {
        case .act(let act): run(act)
        case .tool("clipboard"): withAnimation(KA.M.fade) { mode = .clips }
        case .tool: startVoice()
        }
    }

    // MARK: Running Acts

    func run(_ act: ActItem) {
        var input = target.actInputText
        if act.input == .clipboard || input.isEmpty, hasFullAccess, let clip = UIPasteboard.general.string {
            input = clip
        }
        activeAct = act
        lastResultInput = input
        withAnimation(KA.M.fade) { mode = .panel }
        guard isDemo || store.spendCredit() else {
            panel = .noCredits
            return
        }
        panel = .thinking
        runTask?.cancel()
        runTask = Task { [weak self] in
            guard let self else { return }
            do {
                let output = try await self.ai.run(act, input: input)
                guard !Task.isCancelled else { return }
                self.store.record(act, input: input, output: output)
                withAnimation(KA.M.fade) { self.panel = .result(output) }
                if act.output == .replace {
                    // In-Place Replace は結果を見せてから自動で反映する (録画のデモの挙動)
                    try? await Task.sleep(for: .milliseconds(700))
                    guard !Task.isCancelled, case .result = self.panel else { return }
                    self.apply(.replace)
                }
            } catch is CancellationError {
            } catch {
                self.panel = .error("Something went wrong. Try again.")
            }
        }
    }

    func apply(_ output: ActOutput) {
        guard case .result(let text) = panel, let act = activeAct else { return }
        switch output {
        case .replace: target.replaceAll(with: text)
        case .append: target.append(text)
        case .copy:
            if hasFullAccess { UIPasteboard.general.string = text }
            store.addClip(text)
        }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        closePanel()
        onActApplied?(act, text)
    }

    func closePanel() {
        runTask?.cancel()
        withAnimation(KA.M.fade) {
            mode = .typing
            activeAct = nil
        }
        updateAutoShift()
    }

    // MARK: Voice (モック。キーボード拡張はマイクを使えないため、本家同様に本体アプリ側で録音する想定)

    func startVoice() {
        voiceEmoji = store.state.voice.emoji
        withAnimation(KA.M.fade) {
            mode = .voice
            voiceListening = true
        }
    }

    func finishVoice(askAI: Bool) {
        voiceListening = false
        let polishing = store.state.voice.polishing
        let emoji = voiceEmoji
        let translate = voiceTranslate
        Task { [weak self] in
            guard let self else { return }
            let text = (try? await self.ai.transcribe(polishing: polishing, emoji: emoji, translateTo: translate)) ?? ""
            if askAI {
                self.target.insert(text)
                self.run(ActCatalog.ai)
            } else {
                self.target.insert(text)
                withAnimation(KA.M.fade) { self.mode = .typing }
            }
        }
    }

    func insertClip(_ text: String) {
        target.insert(text)
        feedback()
        withAnimation(KA.M.fade) { mode = .typing }
    }
}
