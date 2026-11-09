# Acti 再現: 差し替え・置き換えたもの

本家の名称・ロゴ・画像・文言はコピーせず、以下のプレースホルダーに置き換えている。
正式なブランドで使う場合は、すべて自前の素材に差し替えること。

| 本家 | 再現での置き換え | 場所 |
|---|---|---|
| アプリ名「Acti」 | 「ShogunAI」 | 全体 |
| A・C・T・I の4つの円のロゴ | **ユーザー提供の ShogunAI ロゴ**（6枚の多角形をベクター化した `ShogunLogoShape`、アプリアイコンも生成） | `Shared/DesignSystem/Components.swift`、`Resources/Assets.xcassets` |
| Acti AI のオーブ（3D の球体アイコン） | `OrbIcon`（ロゴの青系グラデーションの円 + ✦） | 同上 |
| ヒーローカードのキーボードの 3D イラスト | SwiftUI で描いた簡易イラスト | `App/Home/HomeView.swift` |
| Acti 101 の電球イラスト | SF Symbols の組み合わせ | 同上 |
| 各 Act の 3D 風アイコン | `ActIcon`（グラデーションの円 + SF Symbols） | `Shared/DesignSystem/Components.swift` |
| 公式・コミュニティの Act 名と説明文（24件） | 意味の近いダミー名と自作の説明文 | `Shared/Models/Act.swift` |
| 作者名（Acti Official・コミュニティ作者） | 「ShogunAI Official」「ShogunAI Labs」「Mira Notes」 | 同上 |
| オンボーディング・画面のマーケティング文言 | 自作の文言（例: 「Your keys, now with actions」） | `App/Onboarding/` |
| デモのチャット相手「James」とメッセージ | 「Sam」と自作のメッセージ、架空の映画名・映画館名 | `App/Onboarding/DemoChatView.swift`、`Shared/Models/MockAI.swift` |
| 本家のカスタムフォント | Outfit（SIL OFL、`scripts/fetch_font.sh` で取得） | `Resources/Fonts/` |
| 外部リンク（ダウンロード・コミュニティ・規約） | `https://example.com/shogunai/...` | `App/Settings/` |
| WhatsApp / Telegram などのアプリアイコン | SF Symbols と単色の背景 | `App/Onboarding/DemoChatView.swift` |
