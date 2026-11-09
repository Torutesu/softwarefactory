import SwiftUI

// Act Builder Agent: チャットで Act を作る / Remix する (本家ではホーム下部の「Describe an Act to build...」から開く)。
// 録画には映っていないため、公式ガイドの説明 (プラン提示 → 修正 → テスト → 保存) を元にした推定実装。

struct AgentMessage: Identifiable {
    let id = UUID()
    var text: String
    var fromUser: Bool
    var proposal: ActItem?
}

@MainActor
struct AgentChatView: View {
    var initialPrompt: String?
    var remixOf: ActItem?

    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var messages: [AgentMessage] = []
    @State private var draft: ActItem?
    @State private var text = ""
    @State private var thinking = false
    @State private var testing: ActItem?
    @State private var saved = false
    @FocusState private var focused: Bool

    private let ai = MockAI()
    private let suggestions = [
        "Turn my message into a polite email",
        "Summarize copied text in 3 bullets",
        "Reply with a meeting link from my calendar",
    ]

    var body: some View {
        VStack(spacing: 0) {
            NavHeader(
                title: remixOf == nil ? "Build an Act" : "Remix",
                leading: AnyView(CircleIconButton(systemName: "chevron.left") { dismiss() })
            )
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        if messages.isEmpty { emptyState }
                        ForEach(messages) { m in
                            messageView(m).id(m.id)
                        }
                        if thinking {
                            HStack(spacing: 8) {
                                OrbIcon(size: 24, pulsing: true)
                                ThinkingDots()
                            }
                            .id("thinking")
                        }
                    }
                    .padding(KA.S.screenHSettings)
                }
                .onChange(of: messages.count) { _, _ in
                    if let id = messages.last?.id { withAnimation { proxy.scrollTo(id, anchor: .bottom) } }
                }
            }
            inputBar
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .sheet(item: $testing) { act in TryActSheet(act: act) }
        .onAppear {
            if let remixOf, draft == nil {
                var base = remixOf
                base.id = "custom-\(UUID().uuidString.prefix(8))"
                base.name = remixOf.name + " (Remix)"
                base.author = "You"
                base.authorActs = 1
                base.uses = 0
                base.kind = .custom
                base.prompt = remixOf.summary
                base.isOfficial = false
                base.isPublished = false
                draft = base
                messages.append(AgentMessage(text: "Let's remix “\(remixOf.name)”. What would you like to change?", fromUser: false, proposal: base))
            } else if let initialPrompt, messages.isEmpty {
                send(initialPrompt)
            } else {
                focused = true
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 12) {
            OrbIcon(size: 48)
            Text("What should your Act do?").font(KA.F.title).foregroundStyle(KA.C.text)
            Text("Describe it in plain words. I'll propose a plan you can test and save.")
                .font(KA.F.body).foregroundStyle(KA.C.textSecondary)
            ForEach(suggestions, id: \.self) { s in
                Button { send(s) } label: {
                    HStack {
                        Text(s).font(KA.F.body).foregroundStyle(KA.C.text).multilineTextAlignment(.leading)
                        Spacer()
                        Image(systemName: "arrow.up.right").foregroundStyle(KA.C.textSecondary)
                    }
                    .padding(14)
                    .card(radius: KA.R.inner)
                }
                .buttonStyle(.pressable)
            }
        }
        .padding(.top, 20)
    }

    @ViewBuilder
    private func messageView(_ m: AgentMessage) -> some View {
        if m.fromUser {
            HStack {
                Spacer(minLength: 60)
                Text(m.text)
                    .font(KA.F.body)
                    .foregroundStyle(KA.C.ctaText)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(KA.C.ctaBg))
            }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Text(m.text).font(KA.F.body).foregroundStyle(KA.C.text)
                if let p = m.proposal { proposalCard(p) }
            }
        }
    }

    private func proposalCard(_ p: ActItem) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                ActIcon(act: p, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(p.name).font(KA.F.headline).foregroundStyle(KA.C.text)
                    Text("Draft plan").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
            }
            planRow("Input", p.input.title)
            planRow("Instruction", p.prompt.isEmpty ? p.summary : p.prompt)
            if !p.tools.isEmpty { planRow("Apps", p.tools.joined(separator: ", ")) }
            planRow("Output", p.output.title)
            HStack(spacing: 10) {
                Button { testing = p } label: { Label("Test", systemImage: "play") }
                    .buttonStyle(.secondary)
                Button {
                    store.saveCreated(p)
                    saved = true
                    messages.append(AgentMessage(text: "Saved! “\(p.name)” is in My › Created and on your keyboard's Act tab.", fromUser: false))
                } label: { Label(saved && draft?.id == p.id ? "Saved" : "Save", systemImage: "checkmark") }
                    .buttonStyle(.secondary)
            }
        }
        .padding(14)
        .card(radius: KA.R.group, fill: KA.C.cardAlt)
    }

    private func planRow(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(KA.F.micro).foregroundStyle(KA.C.textSecondary)
            Text(value).font(KA.F.body).foregroundStyle(KA.C.text)
        }
    }

    private var inputBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkles").foregroundStyle(KA.C.text)
            TextField(draft == nil ? "Describe an Act to build..." : "Ask for changes...", text: $text, axis: .vertical)
                .font(KA.F.body)
                .foregroundStyle(KA.C.text)
                .lineLimit(1...4)
                .focused($focused)
                .onSubmit { send(text) }
            Button { send(text) } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(KA.C.ctaText)
                    .frame(width: 32, height: 32)
                    .background(Circle().fill(text.isEmpty ? KA.C.textTertiary : KA.C.ctaBg))
            }
            .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty || thinking)
        }
        .padding(.leading, 18).padding(.trailing, 8)
        .frame(minHeight: KA.Size.composer)
        .background(Capsule().fill(KA.C.cardAlt))
        .overlay(Capsule().stroke(KA.C.hairline, lineWidth: 1))
        .padding(.horizontal, KA.S.screenH)
        .padding(.bottom, 6)
    }

    private func send(_ raw: String) {
        let message = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty else { return }
        text = ""
        saved = false
        messages.append(AgentMessage(text: message, fromUser: true))
        thinking = true
        Task {
            let reply = try? await ai.agentReply(to: message, draft: draft)
            thinking = false
            if let reply {
                draft = reply.proposal ?? draft
                messages.append(AgentMessage(text: reply.text, fromUser: false, proposal: reply.proposal))
            }
        }
    }
}
