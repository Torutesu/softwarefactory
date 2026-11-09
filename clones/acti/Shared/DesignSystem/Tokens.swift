import SwiftUI
import UIKit

// デザイントークン。値は docs/app-clone/acti/design-system.md (録画から計測) に対応する。
// 録画はダークモードのみ。ライト側の値は推定 (要検証) で、diff-log.md で追跡する。

extension UIColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1) {
        self.init(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: alpha
        )
    }
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(uiColor: UIColor(hex: hex, alpha: alpha))
    }

    /// ダーク / ライトで値が変わる色
    static func dynamic(dark: UInt32, light: UInt32, darkAlpha: CGFloat = 1, lightAlpha: CGFloat = 1) -> Color {
        Color(uiColor: UIColor { trait in
            trait.userInterfaceStyle == .light
                ? UIColor(hex: light, alpha: lightAlpha)
                : UIColor(hex: dark, alpha: darkAlpha)
        })
    }
}

enum KA {
    // MARK: Color
    enum C {
        static let bg = Color.dynamic(dark: 0x11161D, light: 0xF2F4F8)
        static let bgGradientTop = Color.dynamic(dark: 0x131B28, light: 0xE8EEF7)
        static let card = Color.dynamic(dark: 0x242A31, light: 0xFFFFFF)
        static let cardAlt = Color.dynamic(dark: 0x272C35, light: 0xF7F8FA)
        static let raised = Color.dynamic(dark: 0x2F3742, light: 0xE6E9EF)
        static let segmentTrack = Color.dynamic(dark: 0x2A2F38, light: 0xE3E6EC)
        static let segmentThumb = Color.dynamic(dark: 0x595E6C, light: 0xFFFFFF)
        static let pillAdd = Color.dynamic(dark: 0x034BFF, light: 0x034BFF, darkAlpha: 0.25, lightAlpha: 0.12)
        static let accent = Color(hex: 0x6B95FF)       // 文字用 (暗い背景で読める明るさ)
        static let brand = Color(hex: 0x034BFF)        // ロゴの青
        static let brandHi = Color(hex: 0x4F7DFF)
        static let badgeNewBg = Color(hex: 0x3C6E9C)
        static let badgeNewText = Color(hex: 0xDCE8ED)
        static let ctaBg = Color.dynamic(dark: 0xF1F2F7, light: 0x11161D)
        static let ctaText = Color.dynamic(dark: 0x11161D, light: 0xFFFFFF)
        static let text = Color.dynamic(dark: 0xFFFFFF, light: 0x11161D)
        static let textSecondary = Color.dynamic(dark: 0x989EA5, light: 0x5F6670)
        static let textTertiary = Color.dynamic(dark: 0x5C626B, light: 0xA2A8B0)
        static let divider = Color.dynamic(dark: 0xFFFFFF, light: 0x000000, darkAlpha: 0.06, lightAlpha: 0.06)
        static let hairline = Color.dynamic(dark: 0xFFFFFF, light: 0x000000, darkAlpha: 0.08, lightAlpha: 0.05)
        static let danger = Color(hex: 0xE5484D)
        static let success = Color(hex: 0x34C759)
        static let heroTop = Color(hex: 0xE7EAF2)
        static let heroBottom = Color(hex: 0xC9D3E8)
        static let orb = [Color(hex: 0x9CC0FF), Color(hex: 0x034BFF), Color(hex: 0x5B7CFF), Color(hex: 0xBFD6FF), Color(hex: 0x2F6BFF), Color(hex: 0x9CC0FF)]

        // キーボード (録画のデモ画面から計測)
        static let kbBg = Color.dynamic(dark: 0x26282E, light: 0xE4E6EC, darkAlpha: 0.62, lightAlpha: 0.7)
        static let kbKey = Color.dynamic(dark: 0xFFFFFF, light: 0xFFFFFF, darkAlpha: 0.16, lightAlpha: 0.85)
        static let kbKeySpecial = Color.dynamic(dark: 0xFFFFFF, light: 0x000000, darkAlpha: 0.07, lightAlpha: 0.12)
        static let kbKeyPressed = Color.dynamic(dark: 0xFFFFFF, light: 0xFFFFFF, darkAlpha: 0.32, lightAlpha: 0.5)
        static let kbText = Color.dynamic(dark: 0xFFFFFF, light: 0x000000)
        static let kbPanelBox = Color.dynamic(dark: 0xFFFFFF, light: 0xFFFFFF, darkAlpha: 0.08, lightAlpha: 0.8)
        static let kbHighlight = Color(hex: 0x3D7BF7)

        // デモ用の擬似メッセージアプリ
        static let chatBg = Color.dynamic(dark: 0x000000, light: 0xFFFFFF)
        static let bubbleIn = Color.dynamic(dark: 0x242328, light: 0xE9E9EB)
        static let bubbleOut = Color(hex: 0x1F8BFF)
        static let send = Color(hex: 0x52E76F)
        static let inputBg = Color.dynamic(dark: 0x1B1B1B, light: 0xF2F2F4)
    }

    // MARK: Typography
    // 本家はカスタムの幾何学サンセリフ。再現では Outfit (OFL) を使い、無ければシステムの丸ゴシック系に落とす。
    enum F {
        private static let hasOutfit: Bool = UIFont(name: "Outfit", size: 12) != nil
            || UIFont.fontNames(forFamilyName: "Outfit").isEmpty == false

        static func font(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
            if hasOutfit {
                return .custom("Outfit", size: size).weight(weight)
            }
            return .system(size: size, weight: weight, design: .rounded)
        }

        static let display = font(28, .semibold)
        static let hero = font(26, .semibold)
        static let title = font(20, .semibold)
        static let navTitle = font(18, .semibold)
        static let headline = font(17, .medium)
        static let body = font(15)
        static let bodyMedium = font(15, .medium)
        static let caption = font(13)
        static let micro = font(11, .medium)
        static let button = font(17, .semibold)
        static let chip = font(16, .medium)
    }

    // MARK: Spacing / Radius / Size
    enum S {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let sm: CGFloat = 12
        static let md: CGFloat = 16
        static let lg: CGFloat = 20
        static let xl: CGFloat = 24
        static let xxl: CGFloat = 32
        static let screenH: CGFloat = 12      // ホームのカード
        static let screenHSettings: CGFloat = 16
    }

    enum R {
        static let hero: CGFloat = 28
        static let card: CGFloat = 24
        static let group: CGFloat = 20
        static let inner: CGFloat = 16
        static let key: CGFloat = 8
    }

    enum Size {
        static let circleButton: CGFloat = 44
        static let segmentWidth: CGFloat = 180
        static let heroHeight: CGFloat = 200
        static let promoHeight: CGFloat = 64
        static let rowHeight: CGFloat = 70
        static let rowIcon: CGFloat = 48
        static let primaryButton: CGFloat = 52
        static let chip: CGFloat = 36
        static let addPill: CGFloat = 30
        static let composer: CGFloat = 52
    }

    // MARK: Motion
    enum M {
        static let press = Animation.easeOut(duration: 0.1)
        static let segment = Animation.spring(response: 0.3, dampingFraction: 0.85)
        static let wheelOpen = Animation.spring(response: 0.25, dampingFraction: 0.8)
        static let coachmark = Animation.easeOut(duration: 0.3)
        static let fade = Animation.easeInOut(duration: 0.25)
    }

    // MARK: Keyboard metrics
    enum K {
        static let toolbarHeight: CGFloat = 44
        static let keyHeight: CGFloat = 42
        static let rowSpacing: CGFloat = 12
        static let keySpacing: CGFloat = 6
        static let sidePadding: CGFloat = 3
        static let topPadding: CGFloat = 8
        static let bottomPadding: CGFloat = 4
        static var bodyHeight: CGFloat { topPadding + keyHeight * 4 + rowSpacing * 3 + bottomPadding }
        static var totalHeight: CGFloat { toolbarHeight + bodyHeight }
    }
}
