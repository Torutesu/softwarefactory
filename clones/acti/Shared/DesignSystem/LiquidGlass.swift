import SwiftUI

// リキッドグラス。iOS 26 以降は SwiftUI の glassEffect を使い、それ以前は Material + ハイライトで近似する。
// glassEffect は Xcode 26 (Swift 6.2) の SDK にしか無いため、古い Xcode でもビルドできるようにコンパイラのバージョンで分岐している。

enum GlassStyle {
    /// ボタン・セグメント・チップなどの操作部品
    case control
    /// カードやリストのグループ (透明度を下げて読みやすく)
    case surface
    /// ブランドの青を乗せた主ボタン
    case prominent
}

extension View {
    @ViewBuilder
    func liquidGlass<S: Shape>(_ style: GlassStyle = .control, in shape: S, interactive: Bool = false) -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            self.glassEffect(Self.glass(style, interactive: interactive), in: shape)
        } else {
            self.modifier(GlassFallback(style: style, shape: shape))
        }
        #else
        self.modifier(GlassFallback(style: style, shape: shape))
        #endif
    }

    #if compiler(>=6.2)
    @available(iOS 26.0, *)
    fileprivate static func glass(_ style: GlassStyle, interactive: Bool) -> Glass {
        var g: Glass
        switch style {
        case .control: g = .regular
        case .surface: g = .regular.tint(Color.black.opacity(0.25))
        case .prominent: g = .regular.tint(KA.C.brand)
        }
        if interactive { g = g.interactive() }
        return g
    }
    #endif
}

/// iOS 25 以前の近似: 背景のぼかし + 上辺のハイライト + 縁の光
private struct GlassFallback<S: Shape>: ViewModifier {
    var style: GlassStyle
    var shape: S

    func body(content: Content) -> some View {
        content
            .background {
                ZStack {
                    shape.fill(style == .surface ? .ultraThinMaterial : .thinMaterial)
                    if style == .prominent {
                        shape.fill(LinearGradient(colors: [KA.C.brandHi.opacity(0.75), KA.C.brand.opacity(0.95)], startPoint: .top, endPoint: .bottom))
                    }
                    shape.fill(LinearGradient(colors: [.white.opacity(style == .surface ? 0.08 : 0.16), .clear], startPoint: .top, endPoint: .center))
                }
            }
            .overlay {
                shape.stroke(
                    LinearGradient(colors: [.white.opacity(0.55), .white.opacity(0.08), .white.opacity(0.18)], startPoint: .top, endPoint: .bottom),
                    lineWidth: 1
                )
            }
            .shadow(color: (style == .prominent ? KA.C.brand : Color.black).opacity(style == .prominent ? 0.35 : 0.22), radius: 12, y: 6)
    }
}

/// 背景のアンビエントな青い光。ガラスが色を拾って屈折して見えるように敷く
struct BrandGlowBackground: View {
    var body: some View {
        ZStack {
            LinearGradient(colors: [KA.C.bgGradientTop, KA.C.bg, KA.C.bg], startPoint: .top, endPoint: .bottom)
            GeometryReader { geo in
                let w = geo.size.width, h = geo.size.height
                Circle().fill(KA.C.brand.opacity(0.42)).frame(width: w * 0.9).blur(radius: 70).position(x: w * 0.1, y: h * 0.04)
                Circle().fill(KA.C.brandHi.opacity(0.24)).frame(width: w * 0.8).blur(radius: 80).position(x: w * 0.98, y: h * 0.4)
                Circle().fill(KA.C.brand.opacity(0.2)).frame(width: w * 0.8).blur(radius: 80).position(x: w * 0.2, y: h * 0.9)
            }
        }
        .ignoresSafeArea()
    }
}
