import SwiftUI

// 基本コンポーネント。design-system.md の Components 表に対応。

// MARK: - Buttons

struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(KA.F.button)
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .frame(height: KA.Size.primaryButton)
            .liquidGlass(.prominent, in: Capsule(), interactive: true)
            .opacity(isEnabled ? 1 : 0.35)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(KA.M.press, value: configuration.isPressed)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(KA.F.bodyMedium)
            .foregroundStyle(KA.C.text)
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .liquidGlass(in: RoundedRectangle(cornerRadius: KA.R.inner, style: .continuous), interactive: true)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(KA.M.press, value: configuration.isPressed)
    }
}

struct PressableStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .animation(KA.M.press, value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == PrimaryButtonStyle { static var primary: PrimaryButtonStyle { .init() } }
extension ButtonStyle where Self == SecondaryButtonStyle { static var secondary: SecondaryButtonStyle { .init() } }
extension ButtonStyle where Self == PressableStyle { static var pressable: PressableStyle { .init() } }

@MainActor
struct CircleIconButton: View {
    var systemName: String
    var highlighted = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(KA.C.text)
                .frame(width: KA.Size.circleButton, height: KA.Size.circleButton)
                .liquidGlass(in: Circle(), interactive: true)
                .overlay { if highlighted { Circle().stroke(Color.white, lineWidth: 2) } }
        }
        .buttonStyle(.pressable)
    }
}

// MARK: - Segmented

@MainActor
struct SegmentedPill<Option: Hashable>: View {
    var options: [Option]
    @Binding var selection: Option
    var title: (Option) -> String
    var width: CGFloat = KA.Size.segmentWidth
    var height: CGFloat = KA.Size.circleButton
    @Namespace private var ns

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options, id: \.self) { option in
                let selected = option == selection
                Button {
                    withAnimation(KA.M.segment) { selection = option }
                } label: {
                    Text(title(option))
                        .font(KA.F.headline)
                        .foregroundStyle(selected ? KA.C.text : KA.C.textSecondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background {
                            if selected {
                                Capsule().fill(KA.C.segmentThumb).matchedGeometryEffect(id: "thumb", in: ns)
                            }
                        }
                }
                .buttonStyle(.plain)
            }
        }
        .padding(4)
        .frame(width: width, height: height)
        .liquidGlass(in: Capsule())
    }
}

/// My タブ内の小さいタブ (All / Added / Created)
@MainActor
struct SubTabs<Option: Hashable>: View {
    var options: [Option]
    @Binding var selection: Option
    var title: (Option) -> String

    var body: some View {
        HStack(spacing: 8) {
            ForEach(options, id: \.self) { option in
                let selected = option == selection
                Button { withAnimation(KA.M.segment) { selection = option } } label: {
                    Text(title(option))
                        .font(KA.F.caption.weight(.medium))
                        .foregroundStyle(selected ? KA.C.ctaText : KA.C.textSecondary)
                        .padding(.horizontal, 14)
                        .frame(height: 30)
                        .background(Capsule().fill(selected ? KA.C.ctaBg : KA.C.card))
                }
                .buttonStyle(.plain)
            }
        }
    }
}

// MARK: - Chips / Badges / Pills

@MainActor
struct Chip: View {
    var title: String
    var selected = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(KA.F.chip)
                .foregroundStyle(selected ? .white : KA.C.text)
                .padding(.horizontal, 18)
                .frame(height: KA.Size.chip)
                .liquidGlass(selected ? .prominent : .control, in: Capsule(), interactive: true)
        }
        .buttonStyle(.pressable)
    }
}

@MainActor
struct Badge: View {
    enum Style { case new, free, pro }
    var text: String
    var style: Style

    var body: some View {
        Text(text)
            .font(KA.F.micro)
            .foregroundStyle(foreground)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(badgeBackground)
    }

    private var foreground: Color {
        switch style {
        case .new: KA.C.badgeNewText
        case .free: KA.C.textSecondary
        case .pro: KA.C.ctaText
        }
    }

    @ViewBuilder private var badgeBackground: some View {
        switch style {
        case .new: RoundedRectangle(cornerRadius: 5).fill(KA.C.badgeNewBg)
        case .free: RoundedRectangle(cornerRadius: 5).stroke(KA.C.textTertiary, lineWidth: 1)
        case .pro: RoundedRectangle(cornerRadius: 5).fill(KA.C.ctaBg)
        }
    }
}

@MainActor
struct AddPill: View {
    var added: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(added ? "Added" : "Add")
                .font(KA.F.bodyMedium)
                .foregroundStyle(added ? KA.C.textSecondary : Color(hex: 0xCFE0FF))
                .frame(width: 64, height: KA.Size.addPill)
                .background(Capsule().fill(added ? KA.C.raised.opacity(0.6) : KA.C.pillAdd))
        }
        .buttonStyle(.pressable)
        .sensoryFeedback(.selection, trigger: added)
    }
}

// MARK: - Icons

@MainActor
struct ActIcon: View {
    var act: ActItem
    var size: CGFloat = KA.Size.rowIcon

    var body: some View {
        ZStack {
            Circle().fill(
                LinearGradient(
                    colors: act.colors.map { Color(hex: $0) },
                    startPoint: .topLeading, endPoint: .bottomTrailing
                )
            )
            Circle().fill(.white.opacity(0.18)).padding(size * 0.08).blur(radius: 2)
            Image(systemName: act.symbol)
                .font(.system(size: size * 0.42, weight: .semibold))
                .foregroundStyle(.white)
                .shadow(color: .black.opacity(0.15), radius: 2, y: 1)
        }
        .frame(width: size, height: size)
    }
}

/// Shogun AI を象徴する発光オーブ (本家オーブの見た目を参考にした別デザイン)
@MainActor
struct OrbIcon: View {
    var size: CGFloat = 64
    var pulsing = false
    var grayscale = false
    @State private var phase = false

    var body: some View {
        ZStack {
            Circle()
                .fill(AngularGradient(colors: KA.C.orb, center: .center))
                .blur(radius: size * 0.04)
            Circle()
                .fill(RadialGradient(colors: [.white.opacity(0.85), .clear], center: .topLeading, startRadius: 1, endRadius: size * 0.6))
                .blendMode(.screen)
            RoundedRectangle(cornerRadius: size * 0.16, style: .continuous)
                .fill(.white.opacity(0.22))
                .frame(width: size * 0.46, height: size * 0.46)
                .overlay(
                    Image(systemName: "sparkle")
                        .font(.system(size: size * 0.24, weight: .semibold))
                        .foregroundStyle(.white)
                )
        }
        .frame(width: size, height: size)
        .saturation(grayscale ? 0 : 1)
        .shadow(color: Color(hex: 0x6A7BFF).opacity(grayscale ? 0 : 0.5), radius: size * 0.2)
        .scaleEffect(pulsing && phase ? 1.08 : 1)
        .onAppear {
            guard pulsing else { return }
            withAnimation(.easeInOut(duration: 0.75).repeatForever(autoreverses: true)) { phase = true }
        }
    }
}

/// ブランドマーク (仮)。本家ロゴの代わりに使うプレースホルダー
@MainActor
struct BrandMark: View {
    var size: CGFloat = 40
    var color: Color = KA.C.brand

    var body: some View {
        ShogunLogoShape()
            .fill(color)
            .frame(width: size, height: size * ShogunLogoShape.aspect)
            .accessibilityLabel("ShogunAI")
    }
}

/// ユーザー提供のロゴ (6枚の多角形) をベクターで再現したもの。元画像の座標 (viewBox 280,195 976×630) を正規化している
struct ShogunLogoShape: Shape {
    static let aspect: CGFloat = 630.0 / 976.0
    private static let raw: [[(CGFloat, CGFloat)]] = [
        [(758, 203), (585, 458), (758, 730)],
        [(777, 203), (950, 458), (777, 730)],
        [(287, 302), (567, 468), (737, 730), (464, 594)],
        [(1249, 302), (969, 468), (799, 730), (1068, 594)],
        [(460, 616), (622, 697), (350, 818)],
        [(1075, 616), (914, 697), (1186, 818)],
    ]
    private static let polygons: [[CGPoint]] = raw.map { poly in
        poly.map { CGPoint(x: ($0.0 - 280) / 976, y: ($0.1 - 195) / 630) }
    }

    func path(in rect: CGRect) -> Path {
        var path = Path()
        for poly in Self.polygons {
            guard let first = poly.first else { continue }
            path.move(to: CGPoint(x: rect.minX + first.x * rect.width, y: rect.minY + first.y * rect.height))
            for p in poly.dropFirst() {
                path.addLine(to: CGPoint(x: rect.minX + p.x * rect.width, y: rect.minY + p.y * rect.height))
            }
            path.closeSubpath()
        }
        return path
    }
}

@MainActor
struct ThinkingDots: View {
    @State private var active = 0
    let timer = Timer.publish(every: 0.3, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3) { i in
                Circle()
                    .fill(KA.C.textSecondary)
                    .frame(width: 6, height: 6)
                    .opacity(i == active ? 1 : 0.35)
            }
        }
        .onReceive(timer) { _ in withAnimation(.easeInOut(duration: 0.2)) { active = (active + 1) % 3 } }
    }
}

// MARK: - Containers

struct CardBackground: ViewModifier {
    var radius: CGFloat = KA.R.card
    var fill: Color = KA.C.card

    func body(content: Content) -> some View {
        content
            .background(RoundedRectangle(cornerRadius: radius, style: .continuous).fill(fill.opacity(0.35)))
            .liquidGlass(.surface, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
    }
}

extension View {
    func card(radius: CGFloat = KA.R.card, fill: Color = KA.C.card) -> some View {
        modifier(CardBackground(radius: radius, fill: fill))
    }

    /// 画面背景 (上部にうっすら青いグラデーション)
    func screenBackground() -> some View {
        background(BrandGlowBackground())
    }
}

// MARK: - Act row

@MainActor
struct ActRow: View {
    var act: ActItem
    var showsDivider = false
    var trailing: AnyView? = nil
    @Environment(AppStore.self) private var store

    var body: some View {
        HStack(spacing: 14) {
            ActIcon(act: act)
            VStack(alignment: .leading, spacing: 3) {
                Text(act.name)
                    .font(KA.F.headline)
                    .foregroundStyle(KA.C.text)
                    .lineLimit(1)
                Text(act.uses > 0 ? act.usesLabel : act.summary)
                    .font(KA.F.caption)
                    .foregroundStyle(KA.C.textSecondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            if let trailing {
                trailing
            } else {
                AddPill(added: store.isAdded(act)) { store.toggleAdded(act) }
            }
        }
        .frame(height: KA.Size.rowHeight)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsDivider {
                Rectangle().fill(KA.C.divider).frame(height: 1).padding(.leading, KA.Size.rowIcon + 14)
            }
        }
    }
}

// MARK: - Settings rows

@MainActor
struct SettingsGroup<Content: View>: View {
    var title: String?
    var trailing: AnyView? = nil
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let title {
                HStack {
                    Text(title).font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
                    Spacer()
                    trailing
                }
                .padding(.horizontal, 4)
            }
            VStack(spacing: 0) { content }
                .card(radius: KA.R.group)
        }
    }
}

@MainActor
struct SettingsRow<Accessory: View>: View {
    var icon: String?
    var title: String
    var subtitle: String?
    var titleColor: Color = KA.C.text
    @ViewBuilder var accessory: Accessory

    var body: some View {
        HStack(spacing: 14) {
            if let icon {
                Image(systemName: icon)
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(KA.C.text)
                    .frame(width: 26)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(KA.F.headline).foregroundStyle(titleColor)
                if let subtitle {
                    Text(subtitle).font(KA.F.caption).foregroundStyle(KA.C.textSecondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
            accessory
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(minHeight: 56)
        .contentShape(Rectangle())
    }
}

@MainActor
struct Chevron: View {
    var value: String? = nil
    var body: some View {
        HStack(spacing: 6) {
            if let value { Text(value).font(KA.F.caption).foregroundStyle(KA.C.textSecondary) }
            Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(KA.C.textTertiary)
        }
    }
}

@MainActor
struct ToggleRow: View {
    var icon: String? = nil
    var title: String
    var subtitle: String? = nil
    @Binding var isOn: Bool

    var body: some View {
        SettingsRow(icon: icon, title: title, subtitle: subtitle) {
            Toggle("", isOn: $isOn).labelsHidden().tint(KA.C.accent)
        }
    }
}

/// 段階付きスライダー (Key Label Weight / Haptic / Polishing)
@MainActor
struct StepSlider: View {
    var labels: [String]
    @Binding var value: Int

    var body: some View {
        VStack(spacing: 6) {
            Slider(
                value: Binding(get: { Double(value) }, set: { value = Int($0.rounded()) }),
                in: 0...Double(max(labels.count - 1, 1)), step: 1
            )
            .tint(KA.C.text)
            HStack {
                ForEach(Array(labels.enumerated()), id: \.offset) { i, label in
                    Text(label)
                        .font(KA.F.micro)
                        .foregroundStyle(i == value ? KA.C.text : KA.C.textSecondary)
                    if i < labels.count - 1 { Spacer() }
                }
            }
        }
        .sensoryFeedback(.selection, trigger: value)
    }
}

// MARK: - Coachmark

@MainActor
struct CoachmarkBubble: View {
    var text: String

    var body: some View {
        Text(text)
            .font(KA.F.caption.weight(.medium))
            .foregroundStyle(.black)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(.white))
            .shadow(color: .black.opacity(0.25), radius: 8, y: 2)
            .transition(.opacity.combined(with: .scale(scale: 0.9)))
    }
}

/// タップを促す手のアイコン。小さく上下して押す動きを繰り返す
@MainActor
struct TapHand: View {
    @State private var down = false

    var body: some View {
        Image(systemName: "hand.point.up.left.fill")
            .font(.system(size: 34))
            .foregroundStyle(Color(hex: 0x8FB4FF))
            .shadow(color: .black.opacity(0.3), radius: 4, y: 2)
            .offset(y: down ? 4 : -4)
            .scaleEffect(down ? 0.92 : 1)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) { down = true }
            }
            .allowsHitTesting(false)
    }
}

@MainActor
struct NavHeader: View {
    var title: String
    var leading: AnyView? = nil
    var trailing: AnyView? = nil

    var body: some View {
        ZStack {
            Text(title).font(KA.F.navTitle).foregroundStyle(KA.C.text)
            HStack {
                leading
                Spacer()
                trailing
            }
        }
        .padding(.horizontal, KA.S.screenHSettings)
        .frame(height: 56)
    }
}
