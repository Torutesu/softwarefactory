import SwiftUI

@main
@MainActor
struct KeyactApp: App {
    @State private var store = AppStore.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(store)
                .preferredColorScheme(colorScheme)
                .onChange(of: scenePhase) { _, phase in
                    // キーボード拡張が書いた状態 (フルアクセス・履歴・クレジット) を取り込む
                    if phase == .active { store.reload() }
                }
        }
    }

    private var colorScheme: ColorScheme? {
        switch store.state.appearance {
        case .automatic: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

@MainActor
struct RootView: View {
    @Environment(AppStore.self) private var store
    @State private var showPaywall = false

    var body: some View {
        Group {
            if store.state.onboardingDone {
                HomeView()
                    .transition(.opacity)
            } else {
                OnboardingFlow()
                    .transition(.opacity)
            }
        }
        .animation(KA.M.fade, value: store.state.onboardingDone)
        .onOpenURL { url in
            // キーボードの「Open Keyact」(keyact://upgrade) からペイウォールを開く
            if url.host == "upgrade" { showPaywall = true }
        }
        .sheet(isPresented: $showPaywall) { PaywallView() }
    }
}
