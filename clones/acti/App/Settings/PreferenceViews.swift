import SwiftUI

// S16〜S18 と Preferences 配下の画面。

/// 設定サブ画面の共通の枠 (ScrollView + 背景 + タイトル)
@MainActor
struct SettingsPage<Content: View>: View {
    var title: String
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(spacing: 22) { content }
                .padding(.horizontal, KA.S.screenHSettings)
                .padding(.vertical, 12)
        }
        .screenBackground()
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

@MainActor
private struct RowDivider: View {
    var body: some View { Rectangle().fill(KA.C.divider).frame(height: 1).padding(.leading, 16) }
}

// MARK: - S16 Keyboard Settings

@MainActor
struct KeyboardSettingsView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var store = store
        let k = $store.state.keyboard
        SettingsPage(title: "Keyboard Settings") {
            SettingsGroup(title: "Keyboard") {
                NavigationLink { KeyboardSizeView() } label: {
                    SettingsRow(icon: nil, title: "Keyboard Size & Position") { Chevron() }
                }
                .buttonStyle(.plain)
                RowDivider()
                VStack(alignment: .leading, spacing: 10) {
                    Text("Key Label Weight").font(KA.F.headline).foregroundStyle(KA.C.text)
                    StepSlider(labels: ["Thin", "Default", "Regular", "SemiBold"], value: k.keyLabelWeight)
                }
                .padding(16)
                RowDivider()
                ToggleRow(title: "Always Show Uppercase Keys", subtitle: "Only changes the labels on keys — not what you type.", isOn: k.alwaysUppercase)
                RowDivider()
                ToggleRow(title: "Character Preview", subtitle: "Show a character bubble when you tap a key.", isOn: k.characterPreview)
            }

            SettingsGroup(title: "Feedback") {
                ToggleRow(title: "Haptic Feedback", isOn: k.haptics)
                VStack {
                    StepSlider(labels: ["Low", "Medium", "High"], value: k.hapticStrength)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 14)
                .disabled(!store.state.keyboard.haptics)
                .opacity(store.state.keyboard.haptics ? 1 : 0.4)
                RowDivider()
                ToggleRow(title: "Key Sounds", isOn: k.keySounds)
            }

            SettingsGroup(title: "Input Assistance") {
                ToggleRow(title: "Auto-Capitalization", isOn: k.autoCapitalization)
                RowDivider()
                ToggleRow(title: "\".\" Shortcut", subtitle: "Double-tap the space bar to insert a period.", isOn: k.periodShortcut)
                RowDivider()
                ToggleRow(title: "Predictive Text", isOn: k.predictiveText)
                RowDivider()
                ToggleRow(title: "Next-Word Suggestions", isOn: k.nextWordSuggestions)
            }

            SettingsGroup(title: "Gestures & Keys") {
                ToggleRow(title: "Slide to Type", isOn: k.slideToType)
                RowDivider()
                ToggleRow(title: "Swipe Up for Numbers", subtitle: "Swipe up or long-press top-row keys to enter 1–0.", isOn: k.swipeUpForNumbers)
                RowDivider()
                ToggleRow(title: "Space Bar Cursor Control", subtitle: "Drag the space bar to move the cursor.", isOn: k.spaceCursorControl)
                RowDivider()
                ToggleRow(title: "Backspace Swipe Acceleration", subtitle: "Hold Backspace and drag left to delete faster.", isOn: k.backspaceAcceleration)
                RowDivider()
                ToggleRow(title: "Inline Composition", subtitle: "Show composing text in the app text field.", isOn: k.inlineComposition)
                RowDivider()
                ToggleRow(title: "Emoji Key", isOn: k.emojiKey)
                RowDivider()
                ToggleRow(title: "Comma Key", isOn: k.commaKey)
            }

            SettingsGroup(title: "Access") {
                SettingsRow(icon: nil, title: "Full Access", subtitle: "Required for shared settings and online Acts. We never store what you type.") {
                    Text(store.state.keyboardFullAccess ? "Enabled" : "Off")
                        .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
                RowDivider()
                Button {
                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                } label: {
                    SettingsRow(icon: nil, title: "Paste Access", subtitle: "Used only when an Act requests clipboard content.") { Chevron() }
                }
                .buttonStyle(.plain)
            }
        }
    }
}

@MainActor
struct KeyboardSizeView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Keyboard Size & Position") {
            SettingsGroup(title: "Height") {
                StepSlider(labels: ["Compact", "Default", "Tall"], value: $store.state.keyboard.keyboardHeight)
                    .padding(16)
            }
            Text("Height changes apply the next time the keyboard opens.")
                .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
        }
    }
}

// MARK: - S17 Voice Settings

@MainActor
struct VoiceSettingsView: View {
    @Environment(AppStore.self) private var store
    private let polishingNotes = [
        "Keeps the text close to exactly what you said.",
        "Cleans up filler words while keeping your meaning.",
        "Builds on Clean with better formatting and fills in missing details when the context is clear.",
    ]

    var body: some View {
        @Bindable var store = store
        let v = $store.state.voice
        SettingsPage(title: "Voice Settings") {
            SettingsGroup(title: nil) {
                NavigationLink { LanguagePicker(title: "Spoken Languages", selection: v.spokenLanguages, allowsEmpty: true) } label: {
                    SettingsRow(icon: nil, title: "Spoken Languages", subtitle: "Languages you speak.") {
                        Chevron(value: store.state.voice.spokenLanguages.isEmpty ? "Auto-detect" : store.state.voice.spokenLanguages.joined(separator: ", "))
                    }
                }
                .buttonStyle(.plain)
            }
            SettingsGroup(title: nil) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Polishing").font(KA.F.headline).foregroundStyle(KA.C.text)
                    StepSlider(labels: ["Raw", "Clean", "Smart"], value: v.polishing)
                    Text(polishingNotes[min(store.state.voice.polishing, 2)])
                        .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
                .padding(16)
                RowDivider()
                ToggleRow(title: "Dictation with Emoji", subtitle: "Add emoji that match what you say.", isOn: v.emoji)
            }
            SettingsGroup(title: "Voice Translate") {
                NavigationLink { LanguagePicker(title: "Translate to", selection: v.translateTargets, allowsEmpty: false) } label: {
                    SettingsRow(icon: nil, title: "Translate to", subtitle: "Quick access in Voice Translate.") {
                        Chevron(value: "Languages: \(store.state.voice.translateTargets.count)")
                    }
                }
                .buttonStyle(.plain)
            }
            SettingsGroup(title: "Ask AI") {
                ToggleRow(title: "Enhanced Command", subtitle: "More accurate, up-to-date answers when you need them. Turn off for faster responses.", isOn: v.enhancedCommand)
            }
        }
    }
}

// MARK: - Language

enum Languages {
    static let all = [
        "English", "Japanese", "Spanish", "French", "German", "Italian", "Portuguese", "Korean",
        "Chinese (Simplified)", "Chinese (Traditional)", "Vietnamese", "Thai", "Indonesian", "Hindi",
        "Arabic", "Russian", "Ukrainian", "Turkish", "Dutch", "Polish",
    ]
}

@MainActor
struct LanguageSettingsView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var store = store
        LanguagePicker(title: "Language", selection: $store.state.languages, allowsEmpty: false, footer: "Add two languages to type bilingually without switching keyboards.")
    }
}

@MainActor
struct LanguagePicker: View {
    var title: String
    @Binding var selection: [String]
    var allowsEmpty: Bool
    var footer: String? = nil
    @State private var query = ""

    var body: some View {
        let list = query.isEmpty ? Languages.all : Languages.all.filter { $0.localizedCaseInsensitiveContains(query) }
        SettingsPage(title: title) {
            if allowsEmpty {
                SettingsGroup(title: nil) {
                    Button { selection = [] } label: {
                        SettingsRow(icon: nil, title: "Auto-detect") {
                            if selection.isEmpty { Image(systemName: "checkmark").foregroundStyle(KA.C.accent) }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            SettingsGroup(title: "Languages") {
                ForEach(Array(list.enumerated()), id: \.element) { i, lang in
                    Button { toggle(lang) } label: {
                        SettingsRow(icon: nil, title: lang) {
                            if selection.contains(lang) { Image(systemName: "checkmark").foregroundStyle(KA.C.accent) }
                        }
                    }
                    .buttonStyle(.plain)
                    if i < list.count - 1 { RowDivider() }
                }
            }
            if let footer {
                Text(footer).font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
            }
        }
        .searchable(text: $query)
    }

    private func toggle(_ lang: String) {
        if let i = selection.firstIndex(of: lang) {
            if selection.count > 1 || allowsEmpty { selection.remove(at: i) }
        } else {
            selection.append(lang)
        }
    }
}

// MARK: - S18 Wheel

@MainActor
struct WheelSettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var editingSlot: Int?
    @State private var demoPhase = false

    var body: some View {
        SettingsPage(title: "Keyact Wheel") {
            SettingsGroup(title: "How it works") {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Hold the space bar to open the Wheel").font(KA.F.headline).foregroundStyle(KA.C.text)
                    Text("Slide to choose, release to run").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                    WheelHowItWorks()
                        .frame(height: 170)
                        .clipShape(RoundedRectangle(cornerRadius: KA.R.inner, style: .continuous))
                }
                .padding(16)
            }
            SettingsGroup(title: "Manage Wheel") {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Tap a slot to change it. Hold to remove.").font(KA.F.headline).foregroundStyle(KA.C.text)
                    HStack {
                        ForEach(0..<5, id: \.self) { i in
                            slotView(i)
                            if i < 4 { Spacer() }
                        }
                    }
                    .padding(.horizontal, 8)
                }
                .padding(16)
            }
        }
        .sheet(item: Binding(get: { editingSlot.map(SlotID.init) }, set: { editingSlot = $0?.index })) { slot in
            WheelSlotPicker(slot: slot.index)
        }
    }

    struct SlotID: Identifiable { let index: Int; var id: Int { index } }

    @ViewBuilder
    private func slotView(_ i: Int) -> some View {
        let id = store.state.wheelSlots[i]
        VStack(spacing: 6) {
            Group {
                if i == 2 {
                    OrbIcon(size: 44)
                } else if id == "tool.clipboard" {
                    toolIcon("doc.on.clipboard")
                } else if id == "tool.voice" {
                    toolIcon("mic.fill")
                } else if let act = store.act(id) {
                    ActIcon(act: act, size: 44)
                } else {
                    Image(systemName: "plus")
                        .foregroundStyle(KA.C.textTertiary)
                        .frame(width: 44, height: 44)
                        .background(Circle().stroke(KA.C.textTertiary, style: StrokeStyle(lineWidth: 1, dash: [3])))
                }
            }
            Text(label(i, id)).font(KA.F.micro).foregroundStyle(KA.C.textSecondary).lineLimit(1).frame(width: 56)
        }
        .onTapGesture { if i != 2 { editingSlot = i } }
        .onLongPressGesture { if i != 2 { store.state.wheelSlots[i] = nil } }
    }

    private func toolIcon(_ name: String) -> some View {
        Image(systemName: name)
            .foregroundStyle(KA.C.text)
            .frame(width: 44, height: 44)
            .background(Circle().fill(KA.C.raised))
    }

    private func label(_ i: Int, _ id: String?) -> String {
        if i == 2 { return "Keyact AI" }
        switch id {
        case "tool.clipboard": return "Clipboard"
        case "tool.voice": return "Voice"
        default: return store.act(id)?.name ?? " "
        }
    }
}

/// Wheel の動きを見せる小さなループアニメーション
@MainActor
private struct WheelHowItWorks: View {
    @State private var t = 0
    private let timer = Timer.publish(every: 0.9, on: .main, in: .common).autoconnect()

    var body: some View {
        GeometryReader { geo in
            let c = CGPoint(x: geo.size.width / 2, y: geo.size.height - 8)
            let r = min(geo.size.width * 0.32, 120)
            let angles = [166.0, 128, 90, 52, 14]
            let selected = [nil, 2, 1, 3, nil][t % 5]
            ZStack {
                LinearGradient(colors: [Color(hex: 0xEEF1F7), Color(hex: 0xD9DFEA)], startPoint: .top, endPoint: .bottom)
                Circle().fill(RadialGradient(colors: [.white, Color(hex: 0xBFD2FF).opacity(0.4), .clear], center: .center, startRadius: 10, endRadius: r * 1.3))
                    .frame(width: r * 2.6, height: r * 2.6).position(c)
                ForEach(0..<5, id: \.self) { i in
                    let a = angles[i] * .pi / 180
                    Group {
                        if i == 2 { OrbIcon(size: 34) } else {
                            ActIcon(act: ActCatalog.community[[0, 9, 0, 15, 13][i]], size: 30)
                        }
                    }
                    .scaleEffect(selected == i ? 1.3 : 1)
                    .position(x: c.x + r * cos(a), y: c.y - r * sin(a))
                }
                if let s = selected {
                    let a = angles[s] * .pi / 180
                    TapHand().position(x: c.x + r * 0.7 * cos(a), y: c.y - r * 0.7 * sin(a) + 30)
                }
            }
            .animation(KA.M.segment, value: t)
        }
        .onReceive(timer) { _ in t += 1 }
    }
}

@MainActor
struct WheelSlotPicker: View {
    var slot: Int
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var tab = "My Acts"

    var body: some View {
        NavigationStack {
            VStack(spacing: 14) {
                SubTabs(options: ["My Acts", "Keyact Tools"], selection: $tab) { $0 }
                ScrollView {
                    VStack(spacing: 0) {
                        if tab == "My Acts" {
                            let acts = store.addedActs.filter { $0.id != ActCatalog.aiID }
                            if acts.isEmpty {
                                Text("Add Acts from the home screen first.")
                                    .font(KA.F.body).foregroundStyle(KA.C.textSecondary).padding(30)
                            }
                            ForEach(acts) { act in
                                pick(act.id) { ActRow(act: act, trailing: AnyView(checkmark(act.id))) }
                            }
                        } else {
                            pick("tool.clipboard") {
                                SettingsRow(icon: "doc.on.clipboard", title: "Clipboard") { checkmark("tool.clipboard") }
                            }
                            pick("tool.voice") {
                                SettingsRow(icon: "mic", title: "Voice") { checkmark("tool.voice") }
                            }
                        }
                    }
                    .padding(.horizontal, 12)
                    .card()
                }
            }
            .padding(KA.S.screenHSettings)
            .screenBackground()
            .navigationTitle("Slot \(slot + 1)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }

    private func pick<Label: View>(_ id: String, @ViewBuilder label: () -> Label) -> some View {
        Button {
            store.state.wheelSlots[slot] = id
            dismiss()
        } label: { label() }
            .buttonStyle(.plain)
    }

    @ViewBuilder
    private func checkmark(_ id: String) -> some View {
        if store.state.wheelSlots[slot] == id {
            Image(systemName: "checkmark").foregroundStyle(KA.C.accent)
        }
    }
}

// MARK: - Clips

@MainActor
struct ClipsSettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var editing: Snippet?
    @State private var query = ""

    var body: some View {
        let snippets = store.state.snippets
            .filter { query.isEmpty || $0.text.localizedCaseInsensitiveContains(query) || $0.abbreviation.localizedCaseInsensitiveContains(query) }
            .sorted { $0.pinned && !$1.pinned }
        SettingsPage(title: "Clips") {
            SettingsGroup(title: "Snippets", trailing: AnyView(
                Button { editing = Snippet(text: "") } label: { Image(systemName: "plus").foregroundStyle(KA.C.accent) }
            )) {
                if snippets.isEmpty {
                    Text("Save text you type often. Give it an abbreviation to expand it from the keyboard.")
                        .font(KA.F.caption).foregroundStyle(KA.C.textSecondary).padding(16)
                }
                ForEach(snippets) { s in
                    Button { editing = s } label: {
                        SettingsRow(icon: s.pinned ? "pin.fill" : "text.quote", title: s.text, subtitle: [s.abbreviation, s.category].filter { !$0.isEmpty }.joined(separator: " · ")) { Chevron() }
                    }
                    .buttonStyle(.plain)
                }
            }
            SettingsGroup(title: "Recent Clips", trailing: AnyView(
                Button("Clear") { store.state.clips = [] }.font(KA.F.caption).foregroundStyle(KA.C.accent)
            )) {
                if store.state.clips.isEmpty {
                    Text("Text you copy with Acts appears here.").font(KA.F.caption).foregroundStyle(KA.C.textSecondary).padding(16)
                }
                ForEach(store.state.clips.prefix(20), id: \.self) { c in
                    SettingsRow(icon: "doc.on.doc", title: c) { EmptyView() }
                }
            }
        }
        .searchable(text: $query)
        .sheet(item: $editing) { s in SnippetEditor(snippet: s) }
    }
}

@MainActor
private struct SnippetEditor: View {
    @State var snippet: Snippet
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Text") {
                    TextField("Snippet", text: $snippet.text, axis: .vertical).lineLimit(2...6)
                }
                Section("Abbreviation") {
                    TextField("e.g. addr", text: $snippet.abbreviation)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section {
                    Picker("Category", selection: $snippet.category) {
                        ForEach(["General", "Work", "Personal"], id: \.self) { Text($0) }
                    }
                    Toggle("Pin to top", isOn: $snippet.pinned)
                }
                if store.state.snippets.contains(where: { $0.id == snippet.id }) {
                    Section {
                        Button("Delete Snippet", role: .destructive) {
                            store.state.snippets.removeAll { $0.id == snippet.id }
                            dismiss()
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .screenBackground()
            .navigationTitle("Snippet")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if let i = store.state.snippets.firstIndex(where: { $0.id == snippet.id }) {
                            store.state.snippets[i] = snippet
                        } else {
                            store.state.snippets.insert(snippet, at: 0)
                        }
                        dismiss()
                    }
                    .disabled(snippet.text.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}
