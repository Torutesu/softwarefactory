import SwiftUI

// キーボードの見た目。録画のデモ画面 (57–75s) の配置・色を基準にしている。

@MainActor
struct KeyboardView: View {
    @Bindable var model: KeyboardModel

    var body: some View {
        VStack(spacing: 0) {
            KeyboardToolbar(model: model)
                .frame(height: KA.K.toolbarHeight)
            ZStack {
                switch model.mode {
                case .typing, .wheel:
                    KeysArea(model: model)
                case .panel:
                    ActPanelView(model: model)
                case .acts:
                    ActsPanelView(model: model)
                case .voice:
                    VoicePanelView(model: model)
                case .clips:
                    ClipsPanelView(model: model)
                }
            }
            .frame(height: KA.K.bodyHeight)
            .transition(.opacity)
        }
        .background(KA.C.kbBg)
        .environment(model.store)
    }
}

// MARK: - Toolbar

@MainActor
struct KeyboardToolbar: View {
    @Bindable var model: KeyboardModel

    var body: some View {
        HStack(spacing: 8) {
            if model.mode == .panel {
                Image(systemName: "sparkles").font(.system(size: 15, weight: .semibold))
                Text(model.activeAct?.name ?? "ShogunAI")
                    .font(KA.F.headline)
                    .lineLimit(1)
                Spacer()
                closeButton
            } else {
                TypeActToggle(model: model)
                Spacer()
                if model.isDemo {
                    Text("Demo Keyboard").font(KA.F.micro).foregroundStyle(KA.C.textTertiary)
                    Spacer()
                }
                if model.mode == .typing || model.mode == .wheel {
                    toolbarIcon("doc.on.clipboard") { withAnimation(KA.M.fade) { model.mode = .clips } }
                    Button { model.startVoice() } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "mic.fill").font(.system(size: 12, weight: .semibold))
                            Text("Voice").font(KA.F.caption.weight(.medium))
                        }
                        .foregroundStyle(KA.C.kbText)
                        .padding(.horizontal, 10)
                        .frame(height: 28)
                        .background(Capsule().fill(KA.C.kbKey))
                    }
                    .buttonStyle(.pressable)
                } else {
                    closeButton
                }
            }
        }
        .foregroundStyle(KA.C.kbText)
        .padding(.horizontal, 8)
    }

    private var closeButton: some View {
        toolbarIcon("xmark") { model.closePanel() }
    }

    private func toolbarIcon(_ name: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: name)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(KA.C.kbText)
                .frame(width: 32, height: 28)
                .background(Capsule().fill(KA.C.kbKey))
        }
        .buttonStyle(.pressable)
    }
}

/// 左上の「Type / Act」切り替え
@MainActor
struct TypeActToggle: View {
    @Bindable var model: KeyboardModel

    var body: some View {
        let actsSelected = model.mode == .acts
        HStack(spacing: 0) {
            segment("pencil", "Type", selected: !actsSelected) {
                withAnimation(KA.M.segment) { model.mode = .typing }
            }
            segment("sparkles", "Act", selected: actsSelected) {
                if model.hint == .runActsHere { model.hint = nil }
                withAnimation(KA.M.segment) { model.mode = .acts }
            }
        }
        .padding(2)
        .background(Capsule().fill(KA.C.kbKeySpecial))
        .overlay(alignment: .bottomLeading) {
            if model.hint == .runActsHere {
                VStack(alignment: .leading, spacing: 6) {
                    TapHand().rotationEffect(.degrees(-10))
                    CoachmarkBubble(text: "Run your Acts from here")
                }
                .offset(x: 50, y: 96)
                .fixedSize()
            }
        }
        .zIndex(2)
    }

    private func segment(_ icon: String, _ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 11, weight: .semibold))
                Text(title).font(KA.F.caption.weight(.medium))
            }
            .foregroundStyle(selected ? KA.C.kbText : KA.C.textSecondary)
            .padding(.horizontal, 10)
            .frame(height: 26)
            .background(Capsule().fill(selected ? KA.C.kbKey : .clear))
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Keys

@MainActor
struct KeysArea: View {
    @Bindable var model: KeyboardModel

    private static let letterRows = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
    private static let numberRows = ["1234567890", "-/:;()$&@\"", ".,?!'"]
    private static let symbolRows = ["[]{}#%^*+=", "_\\|~<>€£¥•", ".,?!'"]

    private var rows: [String] {
        switch model.layer {
        case .letters: Self.letterRows
        case .numbers: Self.numberRows
        case .symbols: Self.symbolRows
        }
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width - KA.K.sidePadding * 2
            let unit = (w - KA.K.keySpacing * 9) / 10
            VStack(spacing: KA.K.rowSpacing) {
                row(rows[0], unit: unit)
                row(rows[1], unit: unit)
                HStack(spacing: KA.K.keySpacing) {
                    if model.layer == .letters {
                        ShiftKey(model: model).frame(width: unit * 1.35)
                    } else {
                        SpecialKey(label: model.layer == .numbers ? "#+=" : "123") { model.tapSymbolsSwitch() }
                            .frame(width: unit * 1.35)
                    }
                    Spacer(minLength: 0)
                    HStack(spacing: KA.K.keySpacing) {
                        ForEach(Array(rows[2]), id: \.self) { ch in
                            CharKey(label: String(ch), model: model, numberHint: nil)
                                .frame(width: model.layer == .letters ? unit : unit * 1.38)
                        }
                    }
                    Spacer(minLength: 0)
                    DeleteKey(model: model).frame(width: unit * 1.35)
                }
                bottomRow(unit: unit)
            }
            .padding(.horizontal, KA.K.sidePadding)
            .padding(.top, KA.K.topPadding)
            .padding(.bottom, KA.K.bottomPadding)
            .opacity(model.mode == .wheel ? 0.25 : 1)
            .overlay {
                if model.mode == .wheel {
                    WheelOverlay(model: model, size: geo.size)
                        .transition(.scale(scale: 0.6, anchor: .bottom).combined(with: .opacity))
                }
            }
            .overlay(alignment: .bottom) {
                if model.hint == .holdSpace {
                    VStack(spacing: 4) {
                        CoachmarkBubble(text: "Hold the space bar")
                        TapHand()
                    }
                    .padding(.bottom, 2)
                    .allowsHitTesting(false)
                }
            }
            .coordinateSpace(name: "keys")
            .onAppear { model.keysSize = geo.size }
            .onChange(of: geo.size) { _, s in model.keysSize = s }
        }
    }

    private func row(_ chars: String, unit: CGFloat) -> some View {
        let digits = Array("1234567890")
        return HStack(spacing: KA.K.keySpacing) {
            ForEach(Array(chars.enumerated()), id: \.offset) { i, ch in
                CharKey(
                    label: String(ch), model: model,
                    numberHint: (model.layer == .letters && chars == Self.letterRows[0] && i < digits.count) ? String(digits[i]) : nil
                )
                .frame(width: unit)
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func bottomRow(unit: CGFloat) -> some View {
        HStack(spacing: KA.K.keySpacing) {
            SpecialKey(label: model.layer == .letters ? "123" : "ABC") { model.tapLayerSwitch() }
                .frame(width: unit * 1.35)
            if model.needsGlobe {
                SpecialKey(systemImage: "globe") { model.onGlobe?() }
                    .frame(width: unit * 1.1)
            }
            if model.prefs.emojiKey && !model.needsGlobe {
                SpecialKey(systemImage: "face.smiling") {
                    model.target.insert("🙂")
                }
                .frame(width: unit * 1.1)
            }
            if model.prefs.commaKey {
                CharKey(label: ",", model: model, numberHint: nil).frame(width: unit)
            }
            SpaceKey(model: model)
            SpecialKey(systemImage: "return") { model.tapReturn() }
                .frame(width: unit * 1.9)
        }
    }
}

@MainActor
struct KeyCap<Content: View>: View {
    var pressed: Bool
    var special = false
    @ViewBuilder var content: Content

    var body: some View {
        content
            .frame(maxWidth: .infinity)
            .frame(height: KA.K.keyHeight)
            .background(
                RoundedRectangle(cornerRadius: KA.R.key, style: .continuous)
                    .fill(pressed ? KA.C.kbKeyPressed : (special ? KA.C.kbKeySpecial : KA.C.kbKey))
                    .shadow(color: .black.opacity(0.35), radius: 0, y: 1)
            )
            .foregroundStyle(KA.C.kbText)
            .contentShape(Rectangle())
    }
}

@MainActor
struct CharKey: View {
    let label: String
    @Bindable var model: KeyboardModel
    var numberHint: String?
    @State private var pressed = false
    @State private var longFired = false
    @State private var longTask: Task<Void, Never>?

    private var weight: Font.Weight {
        [.light, .regular, .medium, .semibold][min(model.prefs.keyLabelWeight, 3)]
    }

    private var display: String {
        guard model.layer == .letters else { return label }
        if model.shift != .off || model.prefs.alwaysUppercase { return label.uppercased() }
        return label
    }

    private var assigned: Bool {
        model.layer == .letters && model.store.state.keyAssignments[label] != nil
    }

    var body: some View {
        KeyCap(pressed: pressed) {
            Text(display).font(.system(size: 23, weight: weight))
        }
        .overlay(alignment: .topTrailing) {
            if assigned {
                Image(systemName: "sparkle").font(.system(size: 7, weight: .bold)).foregroundStyle(KA.C.accent).padding(3)
            } else if let numberHint, model.prefs.swipeUpForNumbers {
                Text(numberHint).font(.system(size: 8)).foregroundStyle(KA.C.textSecondary).padding(.top, 2).padding(.trailing, 4)
            }
        }
        .overlay(alignment: .top) {
            if pressed && model.prefs.characterPreview && model.layer == .letters {
                Text(display)
                    .font(.system(size: 32, weight: weight))
                    .foregroundStyle(KA.C.kbText)
                    .frame(width: 52, height: 56)
                    .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(KA.C.kbKeyPressed))
                    .offset(y: -58)
                    .allowsHitTesting(false)
            }
        }
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !pressed else { return }
                    pressed = true
                    longFired = false
                    longTask = Task {
                        try? await Task.sleep(for: .milliseconds(500))
                        guard !Task.isCancelled, pressed else { return }
                        if model.longPressCharacter(label) { longFired = true }
                    }
                }
                .onEnded { value in
                    pressed = false
                    longTask?.cancel()
                    guard !longFired else { return }
                    if value.translation.height < -18, let numberHint, model.prefs.swipeUpForNumbers {
                        model.target.insert(numberHint)
                    } else {
                        model.tapCharacter(label)
                    }
                }
        )
    }
}

@MainActor
struct SpecialKey: View {
    var label: String?
    var systemImage: String?
    var action: () -> Void
    @State private var pressed = false

    init(label: String, action: @escaping () -> Void) {
        self.label = label
        self.action = action
    }

    init(systemImage: String, action: @escaping () -> Void) {
        self.systemImage = systemImage
        self.action = action
    }

    var body: some View {
        KeyCap(pressed: pressed, special: true) {
            if let systemImage {
                Image(systemName: systemImage).font(.system(size: 18, weight: .regular))
            } else if let label {
                Text(label).font(.system(size: 16, weight: .regular))
            }
        }
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in pressed = true }
                .onEnded { _ in
                    pressed = false
                    action()
                }
        )
    }
}

@MainActor
struct ShiftKey: View {
    @Bindable var model: KeyboardModel
    @State private var lastTap: Date?

    var body: some View {
        let icon: String = switch model.shift {
        case .off: "shift"
        case .on: "shift.fill"
        case .locked: "capslock.fill"
        }
        KeyCap(pressed: false, special: model.shift == .off) {
            Image(systemName: icon).font(.system(size: 18, weight: .medium))
        }
        .onTapGesture {
            let now = Date()
            if let lastTap, now.timeIntervalSince(lastTap) < 0.3 {
                model.doubleTapShift()
                self.lastTap = nil
            } else {
                model.tapShift()
                lastTap = now
            }
        }
    }
}

@MainActor
struct DeleteKey: View {
    @Bindable var model: KeyboardModel
    @State private var pressed = false

    var body: some View {
        KeyCap(pressed: pressed, special: true) {
            Image(systemName: pressed ? "delete.left.fill" : "delete.left").font(.system(size: 18, weight: .regular))
        }
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !pressed else { return }
                    pressed = true
                    model.deletePressed()
                }
                .onEnded { _ in
                    pressed = false
                    model.deleteReleased()
                }
        )
    }
}

@MainActor
struct SpaceKey: View {
    @Bindable var model: KeyboardModel
    @State private var pressed = false

    var body: some View {
        KeyCap(pressed: pressed || model.hint == .holdSpace) {
            HStack {
                Spacer()
                Text("space").font(.system(size: 15)).foregroundStyle(KA.C.textSecondary)
                Spacer()
            }
            .overlay(alignment: .bottomTrailing) {
                Text("SHOGUNAI").font(.system(size: 6, weight: .bold)).foregroundStyle(KA.C.textTertiary).padding(4)
            }
        }
        .overlay {
            if model.hint == .holdSpace {
                RoundedRectangle(cornerRadius: KA.R.key, style: .continuous).stroke(KA.C.kbHighlight, lineWidth: 2)
            }
        }
        .gesture(
            DragGesture(minimumDistance: 0, coordinateSpace: .named("keys"))
                .onChanged { value in
                    pressed = true
                    model.spaceChanged(value)
                }
                .onEnded { value in
                    pressed = false
                    model.spaceEnded(value)
                }
        )
    }
}

// MARK: - Wheel

@MainActor
struct WheelOverlay: View {
    @Bindable var model: KeyboardModel
    var size: CGSize

    var body: some View {
        let points = model.wheelSlotPoints(in: size)
        let center = CGPoint(x: size.width / 2, y: size.height - 14)
        let radius = hypot(points[2].x - center.x, points[2].y - center.y)
        ZStack {
            // 半円の背景 (ガラス調)
            Circle()
                .fill(
                    RadialGradient(colors: [Color.white.opacity(0.28), Color(hex: 0x8FB4FF).opacity(0.18), .clear],
                                   center: .center, startRadius: radius * 0.2, endRadius: radius * 1.35)
                )
                .frame(width: radius * 2.7, height: radius * 2.7)
                .position(center)
            Circle()
                .stroke(Color.white.opacity(0.18), lineWidth: 1)
                .frame(width: radius * 2, height: radius * 2)
                .position(center)

            ForEach(0..<5, id: \.self) { i in
                slot(i).position(points[i])
            }

            // 上部のラベル
            Group {
                if let s = model.wheelSelection, let item = model.wheelItem(at: s) {
                    Text(title(item)).font(KA.F.bodyMedium).foregroundStyle(KA.C.kbText)
                } else {
                    Text("Release to cancel").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
            }
            .position(x: size.width / 2, y: 14)

            // 指の位置
            if let p = model.wheelDrag {
                Circle().fill(Color.white.opacity(0.25)).frame(width: 34, height: 34).position(p)
            }
        }
        .frame(width: size.width, height: size.height)
        .allowsHitTesting(false)
    }

    private func title(_ item: KeyboardModel.WheelItem) -> String {
        switch item {
        case .act(let act): act.name
        case .tool("clipboard"): "Clipboard"
        case .tool: "Voice"
        }
    }

    @ViewBuilder
    private func slot(_ i: Int) -> some View {
        let selected = model.wheelSelection == i
        Group {
            switch model.wheelItem(at: i) {
            case .some(.act(let act)) where act.id == ActCatalog.aiID:
                OrbIcon(size: 46)
            case .some(.act(let act)):
                ActIcon(act: act, size: 44)
            case .some(.tool(let name)):
                Image(systemName: name == "clipboard" ? "doc.on.clipboard" : "mic.fill")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(KA.C.kbText)
                    .frame(width: 44, height: 44)
                    .background(Circle().fill(KA.C.kbKey))
            case .none:
                Image(systemName: "plus")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(KA.C.textTertiary)
                    .frame(width: 40, height: 40)
                    .background(Circle().stroke(KA.C.textTertiary, style: StrokeStyle(lineWidth: 1, dash: [3])))
            }
        }
        .scaleEffect(selected ? 1.28 : 1)
        .animation(KA.M.segment, value: selected)
    }
}

// MARK: - Panels

/// Act 実行中 / 結果の HUD (録画 57s の「Thinking •••」)
@MainActor
struct ActPanelView: View {
    @Bindable var model: KeyboardModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: KA.R.inner, style: .continuous)
                    .fill(KA.C.kbPanelBox)
                    .overlay(RoundedRectangle(cornerRadius: KA.R.inner, style: .continuous).stroke(KA.C.hairline, lineWidth: 1))
                content.padding(14)
            }
            if case .result = model.panel, model.activeAct?.output != .replace {
                HStack(spacing: 8) {
                    resultButton("Replace", "arrow.triangle.2.circlepath") { model.apply(.replace) }
                    resultButton("Copy", "doc.on.doc") { model.apply(.copy) }
                    resultButton("Append", "text.append") { model.apply(.append) }
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    @ViewBuilder private var content: some View {
        switch model.panel {
        case .thinking:
            HStack(spacing: 8) {
                Text("Thinking").font(KA.F.body).foregroundStyle(KA.C.textSecondary)
                ThinkingDots()
            }
        case .result(let text):
            ScrollView {
                Text(text)
                    .font(KA.F.body)
                    .foregroundStyle(KA.C.kbText)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
        case .noCredits:
            VStack(alignment: .leading, spacing: 8) {
                Text("You've used today's AI credits").font(KA.F.headline).foregroundStyle(KA.C.kbText)
                Text("Credits reset daily. Upgrade in the app for more.").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                if !model.isDemo {
                    Button("Open ShogunAI") { model.onOpenApp?(URL(string: "shogunai://upgrade")!) }
                        .font(KA.F.bodyMedium)
                        .foregroundStyle(KA.C.accent)
                }
            }
        case .error(let message):
            Text(message).font(KA.F.body).foregroundStyle(KA.C.danger)
        }
    }

    private func resultButton(_ title: String, _ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: icon)
                .font(KA.F.caption.weight(.medium))
                .foregroundStyle(KA.C.kbText)
                .frame(maxWidth: .infinity)
                .frame(height: 36)
                .background(Capsule().fill(KA.C.kbKey))
        }
        .buttonStyle(.pressable)
    }
}

/// Acts タブ (Act Panel)
@MainActor
struct ActsPanelView: View {
    @Bindable var model: KeyboardModel

    var body: some View {
        let acts = model.store.addedActs
        ScrollView {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                ForEach(acts) { act in
                    Button { model.run(act) } label: {
                        HStack(spacing: 8) {
                            if act.id == ActCatalog.aiID { OrbIcon(size: 30) } else { ActIcon(act: act, size: 30) }
                            Text(act.name).font(KA.F.caption.weight(.medium)).foregroundStyle(KA.C.kbText).lineLimit(1)
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, 10)
                        .frame(height: 48)
                        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(KA.C.kbKey))
                    }
                    .buttonStyle(.pressable)
                }
            }
            .padding(10)
            if acts.count <= 1 {
                Text("Add more Acts in the ShogunAI app")
                    .font(KA.F.caption)
                    .foregroundStyle(KA.C.textSecondary)
                    .padding(.top, 4)
            }
        }
    }
}

/// 音声入力パネル (モック)
@MainActor
struct VoicePanelView: View {
    @Bindable var model: KeyboardModel
    @State private var level: CGFloat = 0.3
    let timer = Timer.publish(every: 0.12, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Menu {
                    Button("Off") { model.voiceTranslate = nil }
                    ForEach(model.store.state.voice.translateTargets, id: \.self) { lang in
                        Button(lang) { model.voiceTranslate = lang }
                    }
                } label: {
                    Label("Translate to: \(model.voiceTranslate ?? "Off")", systemImage: "globe")
                        .font(KA.F.caption)
                        .foregroundStyle(KA.C.kbText)
                        .padding(.horizontal, 10).frame(height: 28)
                        .background(Capsule().fill(KA.C.kbKey))
                }
                Spacer()
                Button { model.voiceEmoji.toggle() } label: {
                    Image(systemName: model.voiceEmoji ? "face.smiling.inverse" : "face.smiling")
                        .foregroundStyle(model.voiceEmoji ? KA.C.accent : KA.C.kbText)
                        .frame(width: 32, height: 28)
                        .background(Capsule().fill(KA.C.kbKey))
                }
            }
            Spacer(minLength: 0)
            HStack(spacing: 4) {
                ForEach(0..<24, id: \.self) { i in
                    Capsule()
                        .fill(KA.C.kbText.opacity(0.8))
                        .frame(width: 3, height: model.voiceListening ? max(4, 40 * level * CGFloat((i * 37 % 10) + 3) / 12) : 4)
                }
            }
            .frame(height: 44)
            .animation(.easeInOut(duration: 0.12), value: level)
            Text(model.voiceListening ? "Listening…" : "Processing…")
                .font(KA.F.caption)
                .foregroundStyle(KA.C.textSecondary)
            Spacer(minLength: 0)
            HStack(spacing: 10) {
                Button { model.finishVoice(askAI: false) } label: {
                    Label("Transcribe", systemImage: "text.cursor").frame(maxWidth: .infinity)
                }
                .buttonStyle(VoiceButtonStyle(filled: true))
                Button { model.finishVoice(askAI: true) } label: {
                    Label("Ask AI", systemImage: "sparkles").frame(maxWidth: .infinity)
                }
                .buttonStyle(VoiceButtonStyle(filled: false))
            }
            .disabled(!model.voiceListening)
        }
        .padding(12)
        .onReceive(timer) { _ in level = CGFloat.random(in: 0.2...1) }
    }
}

private struct VoiceButtonStyle: ButtonStyle {
    var filled: Bool
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(KA.F.bodyMedium)
            .foregroundStyle(filled ? KA.C.ctaText : KA.C.kbText)
            .frame(height: 44)
            .background(Capsule().fill(filled ? KA.C.ctaBg : KA.C.kbKey))
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
    }
}

/// Clips (クリップボード履歴 + スニペット)
@MainActor
struct ClipsPanelView: View {
    @Bindable var model: KeyboardModel

    var body: some View {
        let snippets = model.store.state.snippets.sorted { $0.pinned && !$1.pinned }
        let clips = model.store.state.clips
        Group {
            if snippets.isEmpty && clips.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "doc.on.clipboard").font(.system(size: 26)).foregroundStyle(KA.C.textSecondary)
                    Text("Copied text and saved snippets show up here")
                        .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        if !snippets.isEmpty { header("Snippets") }
                        ForEach(snippets) { s in
                            clipRow(s.text, badge: s.abbreviation.isEmpty ? nil : s.abbreviation, pinned: s.pinned)
                        }
                        if !clips.isEmpty { header("Recent") }
                        ForEach(clips, id: \.self) { c in clipRow(c, badge: nil, pinned: false) }
                    }
                    .padding(10)
                }
            }
        }
    }

    private func header(_ t: String) -> some View {
        Text(t).font(KA.F.micro).foregroundStyle(KA.C.textSecondary).padding(.leading, 4)
    }

    private func clipRow(_ text: String, badge: String?, pinned: Bool) -> some View {
        Button { model.insertClip(text) } label: {
            HStack {
                if pinned { Image(systemName: "pin.fill").font(.system(size: 10)).foregroundStyle(KA.C.accent) }
                Text(text).font(KA.F.caption).foregroundStyle(KA.C.kbText).lineLimit(2).multilineTextAlignment(.leading)
                Spacer()
                if let badge { Text(badge).font(KA.F.micro).foregroundStyle(KA.C.textSecondary) }
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(KA.C.kbKey))
        }
        .buttonStyle(.pressable)
    }
}
