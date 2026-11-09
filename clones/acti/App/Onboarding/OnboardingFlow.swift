import SwiftUI
import UIKit

// オンボーディング: 録画 0–86s (S01〜S09)。文言は本家の言い回しを避けたダミー。

enum OnboardingStep: Hashable {
    case intro, fullAccess, ready, actIntro, demo, tryApps
}

struct OnboardingFlow: View {
    @Environment(AppStore.self) private var store
    @State private var step: OnboardingStep = .intro

    var body: some View {
        ZStack {
            switch step {
            case .intro:
                IntroView { withAnimation(KA.M.fade) { step = .fullAccess } }
            case .fullAccess:
                FullAccessView(
                    onClose: { withAnimation(KA.M.fade) { step = .intro } },
                    onDone: { withAnimation(KA.M.fade) { step = .ready } }
                )
            case .ready:
                KeyboardReadyView { withAnimation(KA.M.fade) { step = .actIntro } }
            case .actIntro:
                ActIntroView(act: ActCatalog.ai, onBack: { withAnimation(KA.M.fade) { step = .ready } }) {
                    withAnimation(KA.M.fade) { step = .demo }
                }
            case .demo, .tryApps:
                DemoChatView(finished: step == .tryApps) {
                    withAnimation(KA.M.fade) { step = .tryApps }
                } onFinish: {
                    store.state.onboardingDone = true
                }
            }
        }
    }
}

// MARK: - S01 Intro

struct IntroView: View {
    var onNext: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Spacer().frame(height: 40)
            BrandMark(size: 44)
            Text("Your keys,\nnow with actions")
                .font(KA.F.display)
                .foregroundStyle(KA.C.text)
                .multilineTextAlignment(.center)
                .padding(.top, 20)
            IntroKeyboardDemo()
                .padding(.horizontal, 12)
                .padding(.top, 28)
            Spacer()
            Button("Enable Keyact Keyboard", action: onNext)
                .buttonStyle(.primary)
                .padding(.horizontal, KA.S.screenH)
                .padding(.bottom, 12)
        }
        .screenBackground()
    }
}

/// S01 の自動再生デモ (録画 0–18s): Wheel を開く → Act を選ぶ → 入力欄の文字が変わる、をループ
struct IntroKeyboardDemo: View {
    private struct Beat {
        var text: String
        var wheel: Bool
        var label: String?
        var selected: Int?
    }

    private let beats: [Beat] = [
        Beat(text: "", wheel: true, label: nil, selected: nil),
        Beat(text: "", wheel: true, label: "Instant Meet Link", selected: 1),
        Beat(text: "see you soon", wheel: false, label: nil, selected: nil),
        Beat(text: "see you soon", wheel: true, label: "Emojify", selected: 3),
        Beat(text: "see you soon 🎉✨🙌", wheel: false, label: nil, selected: nil),
        Beat(text: "see you soon 🎉✨🙌", wheel: true, label: "Keyact AI", selected: 2),
        Beat(text: "Welcome to Keyact", wheel: false, label: nil, selected: nil),
    ]
    @State private var index = 0
    private let timer = Timer.publish(every: 2.4, on: .main, in: .common).autoconnect()

    var body: some View {
        let beat = beats[index]
        VStack(spacing: 0) {
            HStack {
                Text(beat.text.isEmpty ? " " : beat.text)
                    .font(.system(size: 14))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 6)
                    .background(beat.wheel ? Color.clear : Color(hex: 0x3D7BF7).opacity(0.18))
                Spacer()
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 24)).foregroundStyle(.black)
            }
            .padding(.horizontal, 10)
            .frame(height: 40)
            .background(Color.white)

            ZStack {
                MiniKeyboard().opacity(beat.wheel ? 0.25 : 1)
                if beat.wheel {
                    MiniWheel(selected: beat.selected, label: beat.label)
                        .transition(.scale(scale: 0.6, anchor: .bottom).combined(with: .opacity))
                }
            }
            .frame(height: 170)
            .background(Color(hex: 0xE1E2E7))
        }
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .animation(KA.M.wheelOpen, value: index)
        .onReceive(timer) { _ in index = (index + 1) % beats.count }
        .accessibilityLabel("Keyboard demo")
    }
}

private struct MiniKeyboard: View {
    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Label("Type", systemImage: "pencil").font(.system(size: 10, weight: .medium))
                Label("Act", systemImage: "sparkles").font(.system(size: 10)).foregroundStyle(.gray)
                Spacer()
                Image(systemName: "doc.on.clipboard").font(.system(size: 10))
                Label("Voice", systemImage: "mic.fill").font(.system(size: 10))
            }
            .foregroundStyle(.black)
            ForEach(["qwertyuiop", "asdfghjkl", "zxcvbnm"], id: \.self) { row in
                HStack(spacing: 5) {
                    ForEach(Array(row), id: \.self) { ch in
                        Text(String(ch))
                            .font(.system(size: 13))
                            .foregroundStyle(.black)
                            .frame(width: 26, height: 28)
                            .background(RoundedRectangle(cornerRadius: 5).fill(.white))
                    }
                }
            }
        }
        .padding(8)
    }
}

private struct MiniWheel: View {
    var selected: Int?
    var label: String?

    var body: some View {
        GeometryReader { geo in
            let c = CGPoint(x: geo.size.width / 2, y: geo.size.height - 6)
            let r: CGFloat = 92
            ZStack {
                Circle().fill(RadialGradient(colors: [.white.opacity(0.9), Color(hex: 0xBFD2FF).opacity(0.5), .clear], center: .center, startRadius: 10, endRadius: r * 1.3))
                    .frame(width: r * 2.6, height: r * 2.6).position(c)
                Text(label ?? "Release to cancel").font(.system(size: 11, weight: label == nil ? .regular : .semibold))
                    .foregroundStyle(label == nil ? .gray : .black)
                    .position(x: c.x, y: 14)
                ForEach(0..<5, id: \.self) { i in
                    let a = [166.0, 128, 90, 52, 14][i] * .pi / 180
                    let p = CGPoint(x: c.x + r * cos(a), y: c.y - r * sin(a))
                    Group {
                        if i == 2 { OrbIcon(size: 34) } else {
                            ActIcon(act: ActCatalog.community[[12, 0, 0, 15, 2][i]], size: 30)
                        }
                    }
                    .scaleEffect(selected == i ? 1.3 : 1)
                    .position(p)
                }
            }
        }
    }
}

// MARK: - S02〜S05 Full Access

struct FullAccessView: View {
    var onClose: () -> Void
    var onDone: () -> Void
    @Environment(\.scenePhase) private var scenePhase
    @Environment(AppStore.self) private var store
    @State private var showGuide = false
    @State private var openedSettings = false
    @State private var showWhat = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                CircleIconButton(systemName: "xmark", action: onClose)
                Spacer()
            }
            .padding(.horizontal, KA.S.screenH)
            .padding(.top, 8)

            Text("Enable Full Access").font(KA.F.display).foregroundStyle(KA.C.text).padding(.top, 8)
            Text("Typing already works. Full Access turns on the AI features.")
                .font(KA.F.caption)
                .foregroundStyle(KA.C.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)
                .padding(.horizontal, 24)

            ZStack {
                PrivacyCard()
                    .opacity(showGuide ? 0 : 1)
                if showGuide {
                    SettingsGuideAnimation()
                        .transition(.scale(scale: 0.4, anchor: .topTrailing).combined(with: .opacity))
                }
            }
            .padding(.horizontal, KA.S.screenH)
            .padding(.top, 24)

            Spacer()
            Button("What does Full Access mean?") { showWhat = true }
                .font(KA.F.caption)
                .underline()
                .foregroundStyle(KA.C.textSecondary)
                .padding(.bottom, 12)
            Button(openedSettings ? "I've turned it on" : "Enable Full Access") {
                if openedSettings {
                    onDone()
                } else {
                    openSettings()
                }
            }
            .buttonStyle(.primary)
            .padding(.horizontal, KA.S.screenH)
            .padding(.bottom, 12)
        }
        .screenBackground()
        .onChange(of: scenePhase) { _, phase in
            // 設定アプリから戻ってきたら次へ (本家も戻った時点で完了画面に進む)
            if phase == .active, openedSettings { onDone() }
        }
        .sheet(isPresented: $showWhat) { FullAccessExplainer() }
    }

    private func openSettings() {
        withAnimation(.spring(response: 0.45, dampingFraction: 0.8)) { showGuide = true }
        Task {
            try? await Task.sleep(for: .milliseconds(1600))
            openedSettings = true
            if let url = URL(string: UIApplication.openSettingsURLString) {
                _ = await UIApplication.shared.open(url)
            }
        }
    }
}

private struct PrivacyCard: View {
    var body: some View {
        VStack(spacing: 14) {
            VStack(spacing: 8) {
                (Text("Your data ") + Text("stays yours.").foregroundColor(KA.C.accent))
                    .font(KA.F.title)
                    .foregroundStyle(KA.C.text)
                Text("Full Access is only used to run AI features. Text or voice is processed only when you trigger one.")
                    .font(KA.F.caption)
                    .foregroundStyle(KA.C.textSecondary)
                    .multilineTextAlignment(.center)
            }
            .padding(18)
            .frame(maxWidth: .infinity)
            .card(radius: KA.R.inner, fill: Color(hex: 0x1E2A3A))

            HStack {
                Text("Allow Full Access").font(KA.F.bodyMedium).foregroundStyle(KA.C.text)
                Spacer()
                Toggle("", isOn: .constant(true)).labelsHidden().tint(KA.C.success).allowsHitTesting(false)
            }
            .padding(.horizontal, 14)
        }
        .padding(14)
        .card(radius: KA.R.card, fill: Color(hex: 0x1A2330))
    }
}

/// S03: 設定アプリのミニチュアが出て「Keyboards」をタップする手が動くガイド
private struct SettingsGuideAnimation: View {
    @State private var tapped = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label("Apps", systemImage: "chevron.left").font(.system(size: 15)).foregroundStyle(.blue)
                Spacer()
                Text("Keyact").font(.system(size: 15, weight: .semibold)).foregroundStyle(.black)
                Spacer()
                Color.clear.frame(width: 50)
            }
            .padding(14)
            Text("ALLOW KEYACT TO ACCESS").font(.system(size: 11)).foregroundStyle(.gray).padding(.horizontal, 16).padding(.top, 6)
            VStack(spacing: 0) {
                guideRow("Siri", "mic.circle.fill", .purple)
                guideRow("Search", "magnifyingglass.circle.fill", .gray)
                guideRow("Cellular Data", "antenna.radiowaves.left.and.right.circle.fill", .green)
                guideRow("Keyboards", "keyboard.fill", .gray, highlight: tapped)
            }
            .background(RoundedRectangle(cornerRadius: 10).fill(.white))
            .padding(12)
        }
        .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Color(hex: 0xF2F2F7)))
        .overlay(alignment: .bottomTrailing) {
            TapHand().padding(.trailing, 40).padding(.bottom, 4)
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 0.4).delay(0.8)) { tapped = true }
        }
    }

    private func guideRow(_ title: String, _ icon: String, _ color: Color, highlight: Bool = false) -> some View {
        HStack {
            Image(systemName: icon).foregroundStyle(color)
            Text(title).font(.system(size: 15)).foregroundStyle(.black)
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(.gray)
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .background(highlight ? Color.gray.opacity(0.2) : .clear)
    }
}

private struct FullAccessExplainer: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("iOS asks every third-party keyboard for “Full Access” before it can use the network. Keyact needs it to run Acts and AI answers.")
                    Text("• Regular typing never leaves your device.\n• Text is sent only when you trigger an Act, Voice or AI.\n• Keystrokes are never logged.")
                    Text("You can turn Full Access off any time in Settings › Apps › Keyact › Keyboards.")
                }
                .font(KA.F.body)
                .foregroundStyle(KA.C.text)
                .padding(20)
            }
            .screenBackground()
            .navigationTitle("Full Access")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - S06 Ready

struct KeyboardReadyView: View {
    var onNext: () -> Void
    @State private var showGuide = false
    @State private var appeared = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer().frame(height: 60)
            Image(systemName: "checkmark")
                .font(.system(size: 26, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 56, height: 56)
                .background(Circle().fill(KA.C.success))
                .scaleEffect(appeared ? 1 : 0.4)
                .opacity(appeared ? 1 : 0)
            Text("Keyact keyboard is ready").font(KA.F.display).foregroundStyle(KA.C.text).padding(.top, 20)

            VStack(spacing: 0) {
                feature("globe", "90+ languages")
                Divider().overlay(KA.C.divider).padding(.leading, 56)
                feature("mic", "Free voice dictation")
                Divider().overlay(KA.C.divider).padding(.leading, 56)
                feature("sparkles", "Thousands of community Acts")
            }
            .card(radius: KA.R.group)
            .padding(.horizontal, KA.S.screenH)
            .padding(.top, 28)
            .opacity(appeared ? 1 : 0)
            .offset(y: appeared ? 0 : 12)

            Spacer()
            HStack(spacing: 4) {
                Text("Keyboard not showing up?").foregroundStyle(KA.C.textSecondary)
                Button("See how") { showGuide = true }.foregroundStyle(KA.C.accent)
            }
            .font(KA.F.caption)
            .padding(.bottom, 14)
            Button("Explore Keyact", action: onNext)
                .buttonStyle(.primary)
                .padding(.horizontal, KA.S.screenH)
                .padding(.bottom, 12)
        }
        .screenBackground()
        .onAppear { withAnimation(.spring(response: 0.5, dampingFraction: 0.7).delay(0.1)) { appeared = true } }
        .sheet(isPresented: $showGuide) { FullAccessExplainer() }
    }

    private func feature(_ icon: String, _ title: String) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icon).font(.system(size: 17)).foregroundStyle(KA.C.accent).frame(width: 26)
            Text(title).font(KA.F.bodyMedium).foregroundStyle(KA.C.text)
            Spacer()
        }
        .padding(.horizontal, 16)
        .frame(height: 54)
    }
}

// MARK: - S07 Act intro

struct ActIntroView: View {
    var act: ActItem
    var onBack: () -> Void
    var onTry: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                CircleIconButton(systemName: "chevron.left", action: onBack)
                Spacer()
            }
            .padding(.horizontal, KA.S.screenH)
            OrbIcon(size: 84, pulsing: true).padding(.top, 8)
            Text(act.name).font(KA.F.title).foregroundStyle(KA.C.text).padding(.top, 14)

            VStack(spacing: 12) {
                AuthorCard(name: act.author, acts: act.authorActs, verified: act.isOfficial, showsFollow: false)
                OverviewCard(text: act.overview)
            }
            .padding(.horizontal, KA.S.screenH)
            .padding(.top, 20)
            Spacer()
            Button("Try It", action: onTry)
                .buttonStyle(.primary)
                .padding(.horizontal, KA.S.screenH)
                .padding(.bottom, 12)
        }
        .screenBackground()
    }
}

struct AuthorCard: View {
    var name: String
    var acts: Int
    var verified = false
    var showsFollow = true
    var following = false
    var onFollow: () -> Void = {}

    var body: some View {
        HStack(spacing: 12) {
            if verified {
                BrandMark(size: 40)
                    .frame(width: 48, height: 48)
                    .background(Circle().fill(Color.black))
            } else {
                Image(systemName: "person.fill")
                    .font(.system(size: 20))
                    .foregroundStyle(KA.C.textSecondary)
                    .frame(width: 48, height: 48)
                    .background(Circle().fill(KA.C.raised))
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(name).font(KA.F.headline).foregroundStyle(KA.C.text)
                    if verified { Image(systemName: "checkmark.seal.fill").font(.system(size: 13)).foregroundStyle(KA.C.accent) }
                }
                Text("\(acts) Acts").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
            }
            Spacer()
            if showsFollow {
                Button(following ? "Following" : "Follow", action: onFollow)
                    .font(KA.F.caption.weight(.semibold))
                    .foregroundStyle(following ? KA.C.text : KA.C.ctaText)
                    .padding(.horizontal, 16)
                    .frame(height: 32)
                    .background(Capsule().fill(following ? KA.C.raised : KA.C.ctaBg))
            }
        }
        .padding(14)
        .card(radius: KA.R.group, fill: KA.C.cardAlt)
    }
}

struct OverviewCard: View {
    var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Act Overview").font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
            Text(text)
                .font(KA.F.body)
                .foregroundStyle(KA.C.text)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(RoundedRectangle(cornerRadius: KA.R.inner, style: .continuous).stroke(KA.C.hairline, lineWidth: 1))
        }
        .padding(14)
        .card(radius: KA.R.group, fill: KA.C.cardAlt)
    }
}
