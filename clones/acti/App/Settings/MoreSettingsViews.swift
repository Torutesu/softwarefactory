import SwiftUI

// S19〜S21: Pro 紹介 (Phone Mic / Desktop Clips)・実行履歴・連携ツール・フィードバック・About

enum ProFeature {
    case phoneMic, desktopClips

    var title: String {
        switch self {
        case .phoneMic: "Phone Mic"
        case .desktopClips: "Desktop Clips"
        }
    }

    var subtitle: String {
        switch self {
        case .phoneMic: "Speak into your phone. Polished text appears on your computer."
        case .desktopClips: "Sync and manage your clips across desktop and mobile."
        }
    }
}

@MainActor
struct ProFeatureView: View {
    var feature: ProFeature
    var asScanner = false
    @Environment(\.dismiss) private var dismiss
    @State private var showPaywall = false

    var body: some View {
        VStack(spacing: 18) {
            if asScanner {
                HStack {
                    Spacer()
                    CircleIconButton(systemName: "xmark") { dismiss() }
                }
            }
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    HStack(spacing: 4) {
                        Text("KEYACT").font(KA.F.micro).foregroundStyle(KA.C.text)
                        Badge(text: "Pro", style: .pro)
                    }
                    Spacer()
                    Text("EXCLUSIVE FEATURE").font(KA.F.micro).foregroundStyle(KA.C.textSecondary)
                }
                Text(feature.title).font(KA.F.display).foregroundStyle(KA.C.text)
                Text(feature.subtitle).font(KA.F.body).foregroundStyle(KA.C.textSecondary)
                diagram
                    .frame(height: 110)
                    .frame(maxWidth: .infinity)
                    .card(radius: KA.R.inner, fill: KA.C.raised.opacity(0.5))
            }
            .padding(18)
            .card(radius: KA.R.card, fill: Color(hex: 0x1C2735))

            if feature == .desktopClips {
                VStack(alignment: .leading, spacing: 6) {
                    Text("One shortcut. Both devices.").font(KA.F.headline).foregroundStyle(KA.C.text)
                    Text("Create shortcuts for your clips and use the same one on your phone and computer.")
                        .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .card(radius: KA.R.group)
            }

            VStack(alignment: .leading, spacing: 10) {
                Text("Connect in two steps").font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
                VStack(spacing: 0) {
                    step(1, "Download Keyact Desktop", "Install and open Keyact on your computer.")
                    Rectangle().fill(KA.C.divider).frame(height: 1).padding(.leading, 52)
                    step(2, "Scan to log in", "Use the Keyact app to scan the desktop QR code.")
                }
                .card(radius: KA.R.group)
            }
            Spacer()
            Button(feature == .phoneMic ? "Login" : "Sign in") { showPaywall = true }
                .buttonStyle(.primary)
        }
        .padding(KA.S.screenHSettings)
        .screenBackground()
        .navigationTitle(asScanner ? "" : feature.title)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showPaywall) { PaywallView() }
    }

    @ViewBuilder private var diagram: some View {
        HStack(spacing: 22) {
            VStack(spacing: 6) {
                Image(systemName: "iphone").font(.system(size: 26))
                Text(feature == .phoneMic ? "Your Voice" : "Phone Clips").font(KA.F.micro)
            }
            if feature == .phoneMic {
                HStack(spacing: 3) {
                    ForEach(0..<9, id: \.self) { i in
                        Capsule().frame(width: 3, height: CGFloat([8, 16, 24, 14, 28, 18, 10, 20, 8][i]))
                    }
                }
            } else {
                Image(systemName: "arrow.left.arrow.right").font(.system(size: 22))
            }
            VStack(spacing: 6) {
                Image(systemName: "desktopcomputer").font(.system(size: 26))
                Text(feature == .phoneMic ? "Polished Text" : "Desktop Clips").font(KA.F.micro)
            }
        }
        .foregroundStyle(KA.C.text)
    }

    private func step(_ n: Int, _ title: String, _ subtitle: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Text("\(n)").font(KA.F.caption.weight(.semibold)).foregroundStyle(KA.C.text)
                .frame(width: 24, height: 24)
                .background(Circle().stroke(KA.C.textTertiary, lineWidth: 1))
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(KA.F.headline).foregroundStyle(KA.C.text)
                Text(subtitle).font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
            }
            Spacer()
        }
        .padding(16)
    }
}

@MainActor
struct ExecutionHistoryView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        let history = store.state.history
        SettingsPage(title: "Execution History") {
            if history.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "clock").font(.system(size: 32)).foregroundStyle(KA.C.textTertiary)
                    Text("Acts you run will show up here").font(KA.F.body).foregroundStyle(KA.C.textSecondary)
                }
                .padding(.top, 80)
            } else {
                SettingsGroup(title: "Recent Runs", trailing: AnyView(
                    Button("Clear") { store.state.history = [] }.font(KA.F.caption).foregroundStyle(KA.C.accent)
                )) {
                    ForEach(history) { run in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(run.actName).font(KA.F.headline).foregroundStyle(KA.C.text)
                                Spacer()
                                Text(run.date, style: .relative).font(KA.F.micro).foregroundStyle(KA.C.textTertiary)
                            }
                            if !run.input.isEmpty {
                                Text(run.input).font(KA.F.caption).foregroundStyle(KA.C.textSecondary).lineLimit(1)
                            }
                            Text(run.output).font(KA.F.caption).foregroundStyle(KA.C.text).lineLimit(2)
                        }
                        .padding(16)
                        .overlay(alignment: .bottom) { Rectangle().fill(KA.C.divider).frame(height: 1) }
                    }
                }
            }
        }
    }
}

@MainActor
struct ConnectedToolsView: View {
    @Environment(AppStore.self) private var store
    @State private var connecting: String?

    private let tools: [(String, String)] = [
        ("Calendar", "calendar"), ("Mail", "envelope"), ("Notes", "note.text"),
        ("Chat", "bubble.left.and.bubble.right"), ("Docs", "doc.text"), ("Tasks", "checklist"),
    ]

    var body: some View {
        SettingsPage(title: "Connected Tools") {
            Text("Connect an app once and reuse it across your Acts.")
                .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                .frame(maxWidth: .infinity, alignment: .leading)
            SettingsGroup(title: nil) {
                ForEach(tools, id: \.0) { tool in
                    let connected = store.state.connectedTools.contains(tool.0)
                    SettingsRow(icon: tool.1, title: tool.0, subtitle: connected ? "Connected (demo)" : nil) {
                        if connecting == tool.0 {
                            ProgressView()
                        } else {
                            Button(connected ? "Disconnect" : "Connect") { toggle(tool.0) }
                                .font(KA.F.caption.weight(.semibold))
                                .foregroundStyle(connected ? KA.C.textSecondary : KA.C.accent)
                        }
                    }
                }
            }
            Text("Demo build: connections are simulated and no account data is accessed.")
                .font(KA.F.micro).foregroundStyle(KA.C.textTertiary)
        }
    }

    private func toggle(_ name: String) {
        if let i = store.state.connectedTools.firstIndex(of: name) {
            store.state.connectedTools.remove(at: i)
            return
        }
        connecting = name
        Task {
            try? await Task.sleep(for: .milliseconds(900))   // OAuth の往復の代わり
            store.state.connectedTools.append(name)
            connecting = nil
        }
    }
}

@MainActor
struct FeedbackView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var kind = "Idea"
    @State private var sent = false

    var body: some View {
        SettingsPage(title: "Feedback") {
            SubTabs(options: ["Idea", "Bug", "Other"], selection: $kind) { $0 }
                .frame(maxWidth: .infinity, alignment: .leading)
            TextField("Tell us what you think…", text: $text, axis: .vertical)
                .font(KA.F.body)
                .foregroundStyle(KA.C.text)
                .lineLimit(6...12)
                .padding(14)
                .card(radius: KA.R.inner)
            Button(sent ? "Thanks!" : "Send") {
                sent = true
                Task {
                    try? await Task.sleep(for: .milliseconds(800))
                    dismiss()
                }
            }
            .buttonStyle(.primary)
            .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty || sent)
        }
    }
}

@MainActor
struct AboutView: View {
    var body: some View {
        SettingsPage(title: "About") {
            VStack(spacing: 10) {
                BrandMark(size: 56)
                    .frame(width: 84, height: 84)
                    .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(.black))
                Text("Keyact").font(KA.F.title).foregroundStyle(KA.C.text)
                Text("Version \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.1.0")")
                    .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
            }
            .padding(.top, 20)
            SettingsGroup(title: nil) {
                Link(destination: URL(string: "https://example.com/keyact/terms")!) {
                    SettingsRow(icon: nil, title: "Terms of Service") { Chevron() }
                }
                Rectangle().fill(KA.C.divider).frame(height: 1).padding(.leading, 16)
                Link(destination: URL(string: "https://example.com/keyact/privacy")!) {
                    SettingsRow(icon: nil, title: "Privacy Policy") { Chevron() }
                }
            }
            Text("A study build that recreates the interaction design of an agentic keyboard. Not affiliated with any existing product.")
                .font(KA.F.micro).foregroundStyle(KA.C.textTertiary).multilineTextAlignment(.center)
        }
    }
}
