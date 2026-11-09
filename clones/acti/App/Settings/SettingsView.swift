import SwiftUI

// S15: Settings (録画 89–137s)。項目の並び・グループは録画どおり。

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var showPaywall = false
    @State private var showScanner = false
    @State private var confirmDelete = false

    var body: some View {
        @Bindable var store = store
        NavigationStack {
            ScrollView {
                VStack(spacing: 22) {
                    AccountCard { showPaywall = true }

                    SettingsGroup(title: "Preferences") {
                        link("globe", "Language") { LanguageSettingsView() }
                        link("keyboard", "Type") { KeyboardSettingsView() }
                        link("mic", "Voice") { VoiceSettingsView() }
                        link("circle.circle", "Wheel") { WheelSettingsView() }
                        link("doc.on.clipboard", "Clips", last: true) { ClipsSettingsView() }
                    }

                    SettingsGroup(
                        title: "Desktop Features",
                        trailing: AnyView(Link("Download", destination: URL(string: "https://example.com/keyact/desktop")!).font(KA.F.caption).foregroundStyle(KA.C.accent))
                    ) {
                        link("iphone.radiowaves.left.and.right", "Phone Mic") { ProFeatureView(feature: .phoneMic) }
                        link("laptopcomputer.and.iphone", "Desktop Clips", last: true) { ProFeatureView(feature: .desktopClips) }
                    }

                    SettingsGroup(title: "Activity & Tools") {
                        link("clock.arrow.circlepath", "Execution History", subtitle: "Recent Runs") { ExecutionHistoryView() }
                        link("point.3.connected.trianglepath.dotted", "Connected Tools", last: true) { ConnectedToolsView() }
                    }

                    SettingsGroup(title: "Support") {
                        link("bubble.left.and.text.bubble.right", "Feedback", subtitle: "Help improve Keyact") { FeedbackView() }
                        Link(destination: URL(string: "https://example.com/keyact/community")!) {
                            SettingsRow(icon: "person.3", title: "Community") { Chevron() }
                        }
                        divider
                        SettingsRow(icon: "circle.lefthalf.filled", title: "Appearance") {
                            Picker("", selection: $store.state.appearance) {
                                ForEach(AppearanceMode.allCases) { Text($0.title).tag($0) }
                            }
                            .pickerStyle(.menu)
                            .tint(KA.C.textSecondary)
                        }
                        divider
                        link("info.circle", "About", last: true) { AboutView() }
                    }

                    Button { confirmDelete = true } label: {
                        VStack(spacing: 6) {
                            Text("Delete Guest Data").font(KA.F.headline).foregroundStyle(KA.C.danger)
                            Text("Permanently deletes your guest data from Keyact and this device. This can't be undone.")
                                .font(KA.F.caption).foregroundStyle(KA.C.textSecondary).multilineTextAlignment(.center)
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity)
                        .card(radius: KA.R.group)
                    }
                    .buttonStyle(.pressable)
                }
                .padding(.horizontal, KA.S.screenHSettings)
                .padding(.bottom, 30)
            }
            .screenBackground()
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    CircleIconButton(systemName: "xmark") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    CircleIconButton(systemName: "qrcode.viewfinder") { showScanner = true }
                }
            }
            .alert("Delete guest data?", isPresented: $confirmDelete) {
                Button("Delete", role: .destructive) {
                    store.resetGuestData()
                    dismiss()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Your Acts, settings and history will be removed and onboarding will start again.")
            }
        }
        .sheet(isPresented: $showPaywall) { PaywallView() }
        .sheet(isPresented: $showScanner) { ProFeatureView(feature: .desktopClips, asScanner: true) }
    }

    private var divider: some View {
        Rectangle().fill(KA.C.divider).frame(height: 1).padding(.leading, 56)
    }

    @ViewBuilder
    private func link<Destination: View>(
        _ icon: String, _ title: String, subtitle: String? = nil, last: Bool = false,
        @ViewBuilder destination: @escaping () -> Destination
    ) -> some View {
        NavigationLink {
            destination()
        } label: {
            SettingsRow(icon: icon, title: title, subtitle: subtitle) { Chevron() }
        }
        .buttonStyle(.plain)
        if !last { divider }
    }
}

struct AccountCard: View {
    @Environment(AppStore.self) private var store
    var onUpgrade: () -> Void

    var body: some View {
        let remaining = store.creditsRemaining
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                Image(systemName: "person.fill")
                    .font(.system(size: 22))
                    .foregroundStyle(KA.C.textSecondary)
                    .frame(width: 52, height: 52)
                    .background(Circle().fill(KA.C.raised))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Sign in").font(KA.F.title).foregroundStyle(KA.C.text)
                    Text("Sign in to sync your Acts").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(KA.C.textTertiary)
            }
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("\(remaining)").font(KA.F.font(22, .semibold)).foregroundStyle(KA.C.text)
                Text("/ \(AppStore.dailyCredits) AI Credits").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                Spacer()
                Text("Resets daily").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(KA.C.raised)
                    Capsule().fill(KA.C.text).frame(width: geo.size.width * CGFloat(remaining) / CGFloat(AppStore.dailyCredits))
                }
            }
            .frame(height: 4)
            .animation(.easeOut, value: remaining)
            HStack {
                HStack(spacing: 6) {
                    Text("KEYACT").font(KA.F.micro).foregroundStyle(KA.C.textSecondary)
                    Badge(text: "Free", style: .free)
                }
                Spacer()
                Button("UPGRADE", action: onUpgrade)
                    .font(KA.F.caption.weight(.bold))
                    .foregroundStyle(KA.C.ctaText)
                    .padding(.horizontal, 16)
                    .frame(height: 32)
                    .background(Capsule().fill(KA.C.ctaBg))
            }
        }
        .padding(16)
        .card(radius: KA.R.group)
    }
}

// MARK: - Paywall (表示のみ。購入処理はしない)

struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var annual = true

    var body: some View {
        VStack(spacing: 20) {
            HStack {
                Spacer()
                CircleIconButton(systemName: "xmark") { dismiss() }
            }
            OrbIcon(size: 72, pulsing: true)
            HStack(spacing: 6) {
                Text("KEYACT").font(KA.F.title).foregroundStyle(KA.C.text)
                Badge(text: "Pro", style: .pro)
            }
            VStack(alignment: .leading, spacing: 12) {
                benefit("bolt.fill", "More AI credits every day")
                benefit("cpu", "Advanced models for Acts and Ask AI")
                benefit("laptopcomputer", "Desktop app, Phone Mic and Desktop Clips")
                benefit("sparkles", "Priority access to new features")
            }
            .padding(16)
            .card(radius: KA.R.group)
            VStack(spacing: 10) {
                plan(title: "Annual", price: "$94.99 / year", note: "Save 20%", selected: annual) { annual = true }
                plan(title: "Monthly", price: "$9.99 / month", note: nil, selected: !annual) { annual = false }
            }
            Spacer()
            Button("Continue") {}
                .buttonStyle(.primary)
                .disabled(true)
            Text("Demo build — purchases are disabled.")
                .font(KA.F.caption).foregroundStyle(KA.C.textTertiary)
        }
        .padding(KA.S.screenHSettings)
        .screenBackground()
    }

    private func benefit(_ icon: String, _ text: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).foregroundStyle(KA.C.accent).frame(width: 22)
            Text(text).font(KA.F.body).foregroundStyle(KA.C.text)
        }
    }

    private func plan(title: String, price: String, note: String?, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(selected ? KA.C.accent : KA.C.textTertiary)
                Text(title).font(KA.F.headline).foregroundStyle(KA.C.text)
                if let note { Badge(text: note, style: .new) }
                Spacer()
                Text(price).font(KA.F.body).foregroundStyle(KA.C.textSecondary)
            }
            .padding(16)
            .background(RoundedRectangle(cornerRadius: KA.R.inner, style: .continuous).fill(KA.C.card))
            .overlay(RoundedRectangle(cornerRadius: KA.R.inner, style: .continuous).stroke(selected ? KA.C.accent : KA.C.hairline, lineWidth: selected ? 2 : 1))
        }
        .buttonStyle(.pressable)
    }
}
