# ShogunAI — Acti（エージェントキーボード）の再現

`/app-clone Acti` で作った iPhone 向けの再現コードです。調査・解析の資料は
[`docs/app-clone/acti/`](../../docs/app-clone/acti/) にあります。

- **スコープ**: iPhone のみ・無料範囲のみ（Mac 版と Pro 機能は対象外）
- **名前・ロゴ・文言**: 本家のものは使わず、仮名「ShogunAI」と仮デザインに置き換えています（[`assets-todo.md`](../../docs/app-clone/acti/assets-todo.md)）
- **AI**: ネットワークを使わないモック（`Shared/Models/MockAI.swift`）。実際の LLM に差し替えるときは `AIService` を実装して注入します

## ブラウザで見るプロトタイプ

`prototype/index.html` は、SwiftUI 実装と同じトークン・レイアウト・流れで作った HTML 版のプロトタイプです（Xcode なしで触れる確認用）。AI の応答は固定、購入は無効です。

## デザイン

- ロゴはユーザー提供の ShogunAI ロゴをベクター化して使用（`ShogunLogoShape`）。アプリアイコンも同じロゴから生成
- リキッドグラス: iOS 26 + Xcode 26 では `glassEffect` を使い、それ以外では見た目を近似（`Shared/DesignSystem/LiquidGlass.swift`）

## 動かし方（Mac + Xcode 15 以上、リキッドグラス本来の表示は Xcode 26 + iOS 26）

```bash
brew install xcodegen
```

```bash
cd clones/acti && ./scripts/fetch_font.sh && xcodegen generate && open ShogunAI.xcodeproj
```

1. `fetch_font.sh` は代替フォント Outfit（SIL OFL）を取得します。無くても動きます（システムの丸ゴシック系で表示）
2. Xcode で `ShogunAI` と `ShogunAIKeyboard` の両ターゲットに自分の Team を設定します
3. App Group `group.com.example.shogunai` を両ターゲットで有効にします（Bundle ID を変える場合は `project.yml` と `Shared/Models/Store.swift` の `AppGroup.id` も合わせて変更）
4. 実機またはシミュレータ（iOS 17 以上）で `ShogunAI` を実行します
5. 設定 › 一般 › キーボード › キーボード › 新しいキーボードを追加 › ShogunAI、フルアクセスを ON

## 構成

| パス | 内容 |
|---|---|
| `project.yml` | XcodeGen の定義（アプリ + キーボード拡張、App Group 共有） |
| `Shared/DesignSystem/Tokens.swift` | デザイントークン（色・文字・余白・角丸・モーション・キーボード寸法） |
| `Shared/DesignSystem/Components.swift` | 基本コンポーネント（ボタン・セグメント・チップ・Act 行・設定行・オーブ・コーチマーク） |
| `Shared/Models/` | Act のカタログ、共有ストア（クレジット・履歴・Wheel・キー割り当て・設定）、モック AI |
| `Shared/Keyboard/` | キーボード本体（キー配列・Wheel・Act パネル・Acts タブ・Voice・Clips）。アプリ内デモと拡張で共用 |
| `Keyboard/KeyboardViewController.swift` | キーボード拡張のエントリ（`UITextDocumentProxy` を接続） |
| `App/Onboarding/` | イントロ → フルアクセス → 準備完了 → Act 紹介 → 擬似チャットのデモ → 完了カード |
| `App/Home/` | ホーム（All / My）、カテゴリ一覧、検索、Act 詳細、Try It、キー割り当て、Act Builder Agent、Tips |
| `App/Settings/` | 設定（アカウント・クレジット、Language / Type / Voice / Wheel / Clips、Pro 紹介、履歴、連携、フィードバック、外観、About、ゲストデータ削除）、ペイウォール（表示のみ） |

## 再現している主なインタラクション

- **スペースバー**: タップで空白、横にドラッグでカーソル移動、長押しで Wheel（スライドで選択、離して実行、外で離すとキャンセル）
- **Act の実行**: 「Thinking •••」→ 結果。出力が「置き換え」の Act は結果を見せてから自動で反映し、それ以外は Replace / Copy / Append を選ぶ
- **キーの長押し**: Act を割り当てたキー（小さな ✦ が付く）を長押しで実行。上段は上スワイプで数字
- **その他**: 自動大文字化、スペース2回でピリオド、削除キーの長押しで加速（途中から単語単位）、文字プレビュー、触覚・クリック音、1日10クレジットと切れたときの表示、実行履歴
- **オンボーディングのデモ**: 本家と同じく、擬似メッセージアプリの中で「スペース長押し → AI で入力欄を置き換え → 送信 → Act タブから実行 → 送信 → 完了カード」を体験する

## 既知の制限

- **ビルド未確認**: クラウド環境（Linux）で作成したため、Xcode でのビルドはまだしていません。Swift の構文チェックのみ済みです。最初のビルドで型エラーが出たら `diff-log.md` に記録して直します
- **音声入力はモック**: iOS のキーボード拡張はマイクを使えません。本家は本体アプリ側で録音していると推定されます。ここでは固定の文章を返します
- **拡張から本体アプリを開く処理**（クレジット切れ時の「Open ShogunAI」）は非公開の方法に頼るため、iOS のバージョンによっては開けません
- **録画にない画面は推定**: キーボード本体の通常入力、Acts タブ、Voice、Clips、Agent、Community の検索・インストール、ライトモードは公式ガイドの説明を元にした推定です（`docs/app-clone/acti/analysis.md` の「録画の網羅状況」）
