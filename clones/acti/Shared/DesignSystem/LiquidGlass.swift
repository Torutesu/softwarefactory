import SwiftUI

// リキッドグラス。iOS 26 以降は SwiftUI の glassEffect を使い、それ以前は Material + ハイライトで近似する。
// glassEffect は Xcode 26 (Swift 6.2) の SDK にしか無いため、古い Xcode でもビルドできるようにコンパイラのバージョンで分岐している。

// 使う場所 (iOS 26 の考え方に合わせる): スクロールする内容の上に浮く操作部品だけ。
//   - 上部の丸ボタン・All/My・検索 (HomeView のヘッダー)
//   - 下部の「Describe an Act to build...」バー
//   - Act 詳細の下に浮く Remix / Add
// カード・リスト・設定のグループ (内容レイヤー) には使わない。主ボタンは録画どおり白の不透明。

extension View {
    @ViewBuilder
    func liquidGlass<S: Shape>(in shape: S, interactive: Bool = true) -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            self.glassEffect(interactive ? .regular.interactive() : .regular, in: shape)
        } else {
            self.modifier(GlassFallback(shape: shape))
        }
        #else
        self.modifier(GlassFallback(shape: shape))
        #endif
    }

    /// iOS 26 のスクロールエッジ効果 (内容が浮くバーの下に入ると柔らかくぼける)。それ以前は何もしない
    @ViewBuilder
    func softScrollEdges() -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            self.scrollEdgeEffectStyle(.soft, for: .all)
        } else {
            self
        }
        #else
        self
        #endif
    }
}

/// iOS 25 以前の近似: 薄い Material + 左上の鏡面ハイライト + 縁の光
private struct GlassFallback<S: Shape>: ViewModifier {
    var shape: S

    func body(content: Content) -> some View {
        content
            .background {
                ZStack {
                    shape.fill(.ultraThinMaterial)
                    shape.fill(Color(hex: 0x76809A).opacity(0.14))
                    shape.fill(LinearGradient(colors: [.white.opacity(0.18), .clear, .clear, .white.opacity(0.06)], startPoint: .topLeading, endPoint: .bottomTrailing))
                }
            }
            .overlay {
                shape.stroke(LinearGradient(colors: [.white.opacity(0.5), .white.opacity(0.1), .white.opacity(0.25)], startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 0.75)
            }
            .shadow(color: .black.opacity(0.3), radius: 10, y: 5)
    }
}

/// iOS 26 ではシステムのスクロールエッジ効果に任せ、それ以前だけフェードを敷く
struct LegacyEdgeFade: View {
    var edge: EdgeFade.Edge

    var body: some View {
        if #available(iOS 26.0, *) {
            Color.clear
        } else {
            EdgeFade(edge: edge)
                .padding(edge == .top ? .bottom : .top, -24)
                .ignoresSafeArea()
        }
    }
}

/// スクロールエッジ効果の代わり (iOS 25 以前): 浮くバーの背後で内容をフェードさせる
struct EdgeFade: View {
    enum Edge { case top, bottom }
    var edge: Edge

    var body: some View {
        LinearGradient(colors: [KA.C.bg, KA.C.bg.opacity(0)], startPoint: edge == .top ? .top : .bottom, endPoint: edge == .top ? .bottom : .top)
            .allowsHitTesting(false)
    }
}
