import SwiftUI
import UIKit

// S08〜S09: 擬似メッセージアプリでキーボードを試すインタラクティブデモ (録画 50–86s)。
// 本家と同じ流れ: スペース長押し → AI で入力欄を置き換え → 送信 → Act タブから実行 → 送信 → 完了カード

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    var text: String
    var outgoing: Bool
}

@MainActor
@Observable
final class DemoChatModel {
    enum Stage { case intro, first, firstReady, second, secondReady, done }

    var input = ""
    var messages: [ChatMessage] = []
    var stage: Stage = .intro
    var partnerTyping = false
    var inputSelected = false
    @ObservationIgnored var onComplete: () -> Void = {}
    @ObservationIgnored private(set) var keyboard: KeyboardModel!
    @ObservationIgnored private var target: StringTextTarget!

    init(store: AppStore) {
        let target = StringTextTarget(get: { "" }, set: { _ in })
        self.target = target
        keyboard = KeyboardModel(store: store, target: target, isDemo: true)
        keyboard.ai = MockAI(latency: .milliseconds(1500))
        target.get = { [weak self] in self?.input ?? "" }
        target.set = { [weak self] value in
            self?.input = value
            self?.inputSelected = false
        }
        keyboard.onActApplied = { [weak self] _, _ in self?.actApplied() }
    }

    func start() {
        guard stage == .intro else { return }
        Task {
            partnerTyping = true
            try? await Task.sleep(for: .milliseconds(900))
            partnerTyping = false
            withAnimation(KA.M.fade) {
                messages.append(ChatMessage(text: "Want to catch a movie this weekend? Anything good out? 🍿", outgoing: false))
            }
            try? await Task.sleep(for: .milliseconds(500))
            prefill("Recent movies")
            withAnimation(KA.M.coachmark) { keyboard.hint = .holdSpace }
            stage = .first
        }
    }

    private func prefill(_ text: String) {
        input = text
        inputSelected = true
        target.selectAllOnNextEdit = true
        keyboard.shift = .off
    }

    private func actApplied() {
        switch stage {
        case .first: stage = .firstReady
        case .second: stage = .secondReady
        default: break
        }
    }

    func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        withAnimation(KA.M.fade) { messages.append(ChatMessage(text: text, outgoing: true)) }
        input = ""
        inputSelected = false
        target.selectAllOnNextEdit = false
        keyboard.hint = nil
        let next = stage
        Task {
            try? await Task.sleep(for: .milliseconds(600))
            partnerTyping = true
            try? await Task.sleep(for: .milliseconds(1100))
            partnerTyping = false
            switch next {
            case .intro, .first, .firstReady:
                withAnimation(KA.M.fade) {
                    messages.append(ChatMessage(text: "Ooh, sounds fun! I'll be downtown — any theaters around there?", outgoing: false))
                }
                try? await Task.sleep(for: .milliseconds(500))
                prefill("Nearby cinemas")
                withAnimation(KA.M.coachmark) { keyboard.hint = .runActsHere }
                stage = .second
            default:
                withAnimation(KA.M.fade) {
                    messages.append(ChatMessage(text: "Perfect! Popcorn's on me 🍿", outgoing: false))
                }
                stage = .done
                try? await Task.sleep(for: .milliseconds(900))
                onComplete()
            }
        }
    }
}

@MainActor
struct DemoChatView: View {
    var finished: Bool
    var onComplete: () -> Void
    var onFinish: () -> Void

    @Environment(AppStore.self) private var store
    @State private var model: DemoChatModel?

    var body: some View {
        ZStack {
            if let model {
                content(model)
            }
        }
        .background(KA.C.chatBg.ignoresSafeArea())
        .onAppear {
            guard model == nil else { return }
            let m = DemoChatModel(store: store)
            m.onComplete = onComplete
            model = m
            m.start()
        }
    }

    @ViewBuilder
    private func content(_ model: DemoChatModel) -> some View {
        VStack(spacing: 0) {
            ChatHeader()
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 10) {
                        ForEach(model.messages) { m in
                            ChatBubble(message: m).id(m.id)
                        }
                        if model.partnerTyping {
                            HStack {
                                ThinkingDots()
                                    .padding(.horizontal, 14).padding(.vertical, 12)
                                    .background(RoundedRectangle(cornerRadius: 18).fill(KA.C.bubbleIn))
                                Spacer()
                            }
                            .id("typing")
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                }
                .onChange(of: model.messages.count) { _, _ in
                    if let last = model.messages.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
                }
            }
            ChatComposer(model: model)
            KeyboardView(model: model.keyboard)
        }
        .overlay {
            if finished {
                TryInAppsCard(onFinish: onFinish)
                    .transition(.opacity.combined(with: .scale(scale: 0.95)))
            }
        }
        .animation(KA.M.fade, value: finished)
    }
}

@MainActor
private struct ChatHeader: View {
    var body: some View {
        ZStack {
            VStack(spacing: 4) {
                Text("S")
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                    .frame(width: 52, height: 52)
                    .background(Circle().fill(LinearGradient(colors: [Color(hex: 0x8EC5FC), Color(hex: 0x6A7BFF)], startPoint: .top, endPoint: .bottom)))
                HStack(spacing: 2) {
                    Text("Sam").font(.system(size: 14, weight: .semibold))
                    Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold)).foregroundStyle(.secondary)
                }
                .foregroundStyle(KA.C.text)
                .padding(.horizontal, 12).padding(.vertical, 5)
                .background(Capsule().fill(KA.C.bubbleIn))
            }
            HStack {
                Spacer()
                Image(systemName: "video")
                    .font(.system(size: 17))
                    .foregroundStyle(KA.C.text)
                    .frame(width: 44, height: 44)
                    .background(Circle().fill(KA.C.bubbleIn))
            }
            .padding(.horizontal, 14)
        }
        .padding(.vertical, 6)
    }
}

@MainActor
private struct ChatBubble: View {
    var message: ChatMessage

    var body: some View {
        HStack {
            if message.outgoing { Spacer(minLength: 60) }
            Text(message.text)
                .font(.system(size: 16))
                .foregroundStyle(message.outgoing ? .white : KA.C.text)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(message.outgoing ? KA.C.bubbleOut : KA.C.bubbleIn))
            if !message.outgoing { Spacer(minLength: 60) }
        }
    }
}

@MainActor
private struct ChatComposer: View {
    @Bindable var model: DemoChatModel

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "plus")
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(KA.C.text)
                .frame(width: 38, height: 38)
                .background(Circle().fill(KA.C.bubbleIn))
            HStack {
                Group {
                    if model.input.isEmpty {
                        Text("iMessage").foregroundStyle(KA.C.textTertiary)
                    } else {
                        Text(model.input)
                            .foregroundStyle(KA.C.text)
                            .padding(.horizontal, 2)
                            .background(model.inputSelected ? KA.C.kbHighlight.opacity(0.35) : .clear)
                    }
                }
                .font(.system(size: 16))
                .lineLimit(4)
                Spacer(minLength: 4)
                Button { model.send() } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 30, height: 30)
                        .background(Circle().fill(model.input.isEmpty ? KA.C.textTertiary : KA.C.send))
                }
                .disabled(model.input.isEmpty)
            }
            .padding(.leading, 14)
            .padding(.trailing, 4)
            .padding(.vertical, 4)
            .background(Capsule().fill(KA.C.inputBg))
            .overlay(Capsule().stroke(KA.C.hairline, lineWidth: 1))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }
}

/// S09: 「お気に入りのアプリで試そう」カード
@MainActor
private struct TryInAppsCard: View {
    var onFinish: () -> Void

    private let apps: [(String, String, Color, String)] = [
        ("Messages", "message.fill", Color(hex: 0x34C759), "sms:"),
        ("WhatsApp", "phone.bubble.fill", Color(hex: 0x25D366), "whatsapp://"),
        ("Telegram", "paperplane.fill", Color(hex: 0x2AABEE), "tg://"),
    ]

    var body: some View {
        ZStack {
            Rectangle().fill(.ultraThinMaterial).ignoresSafeArea()
            VStack(spacing: 18) {
                OrbIcon(size: 56)
                Text("Now try it in your\nfavorite apps")
                    .font(KA.F.title)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(KA.C.text)
                VStack(spacing: 8) {
                    ForEach(apps, id: \.0) { app in
                        Button {
                            if let url = URL(string: app.3) { UIApplication.shared.open(url) }
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: app.1)
                                    .font(.system(size: 15))
                                    .foregroundStyle(.white)
                                    .frame(width: 32, height: 32)
                                    .background(RoundedRectangle(cornerRadius: 8).fill(app.2))
                                Text(app.0).font(KA.F.bodyMedium).foregroundStyle(KA.C.text)
                                Spacer()
                                Image(systemName: "arrow.up.right").foregroundStyle(KA.C.accent)
                            }
                            .padding(.horizontal, 14)
                            .frame(height: 52)
                            .card(radius: KA.R.inner, fill: KA.C.cardAlt)
                        }
                        .buttonStyle(.pressable)
                    }
                }
                Button("Finish demo", action: onFinish)
                    .font(KA.F.bodyMedium)
                    .foregroundStyle(KA.C.accent)
                    .padding(.top, 4)
            }
            .padding(22)
            .card(radius: KA.R.hero)
            .padding(.horizontal, 36)
        }
    }
}
