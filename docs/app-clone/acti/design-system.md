# Acti デザインシステム（推定）

録画解像度: 1206×2622 / 論理解像度: 402×874pt / 倍率: @3x。数値はフレームから計測して 4pt 単位に丸めた推定値。
色は H.265 の圧縮後のピクセルの中央値なので ±3 程度の誤差がある。録画はダークモードのみ（ライトモードは未確認）。

## Color（ダーク）
| トークン | 値 | 用途 | 採取元 |
|---|---|---|---|
| color.bg.base | `#11161D` | アプリ全体の背景 | ホーム・設定・詳細 |
| color.bg.gradientTop | `#131B28` | 上部のわずかな青みのグラデーション | ホーム上部 |
| color.surface.card | `#242A31` | リストカード・設定のグループ | ホーム・設定 |
| color.surface.cardAlt | `#272C35` | 詳細の Overview 枠、下部の入力バー | Act 詳細・ホーム |
| color.surface.raised | `#2F3742` | 円形アイコンボタン・Remix / Add ボタン | Act 詳細 |
| color.surface.segmentTrack | `#2A2F38` 前後 | All/My のトラック（半透明） | ホーム |
| color.surface.segmentThumb | `#595E6C` | 選択中のセグメント | ホーム |
| color.surface.pillAdd | `#2A3644` | 「Add」ピルの背景 | ホーム |
| color.accent.blue | `#5094D7` | 「Add」の文字・リンク・Download | ホーム・設定 |
| color.accent.badgeNew | `#3C6E9C` 前後（文字 `#DCE8ED`） | 「New」バッジ | Acti 101 カード |
| color.cta.primaryBg | `#F1F2F7` | 主ボタン（Try It・Enable…・Sign in） | 各所 |
| color.cta.primaryText | `#11161D` | 主ボタンの文字 | 各所 |
| color.text.primary | `#FFFFFF` | 見出し・本文 | — |
| color.text.secondary | `#989EA5` | 説明・`uses`・More | — |
| color.text.tertiary | `#5C626B` 前後 | 無効・プレースホルダー | — |
| color.danger | `#E5484D` 前後 | Delete Guest Data | 設定 |
| color.success | `#34C759`（iOS システム） | トグル ON・準備完了のチェック | — |
| color.hero.gradient | `#E7EAF2 → #C9D3E8` | 「Acti AI」ヒーローカード（明るいガラス調） | ホーム |
| color.orb | 青〜紫〜シアンのグラデーション + 内側のハイライト | Acti AI のオーブアイコン | ホーム・Wheel |
| color.demo.keyboardPanel | `#222222` / 枠 `#303030` | デモのキーボード上の Acti パネル | デモ |
| color.demo.send | `#52E76F` | デモの送信ボタン | デモ |

## Typography
画面のフォントは丸みのある幾何学的なサンセリフ（Outfit / Lexend 系）。数字と英字の形から、システムフォントではなくカスタムフォントと判断。
**再現では無料フォント「Outfit」（Google Fonts、OFL）を代わりに使う。** iOS 設定アプリ部分はシステムフォント。

| トークン | サイズ (pt) | ウェイト | 用途 |
|---|---|---|---|
| font.display | 28 | SemiBold | 「Your keyboard can Act」「Acti keyboard is ready」 |
| font.title | 20 | SemiBold | ナビゲーションタイトル・セクション見出し（Writing） |
| font.heroTitle | 26 | SemiBold | ヒーローの「Acti AI」 |
| font.headline | 17 | Medium | リスト行の名前・設定の行 |
| font.body | 15 | Regular | 説明文 |
| font.caption | 13 | Regular | `11.8K uses`・補足説明 |
| font.micro | 11 | Medium | バッジ（New・Free・Pro） |
| font.button | 17 | SemiBold | 主ボタン |
| font.chip | 16 | Medium | カテゴリチップ |

## Spacing / Size / Radius
- 横の余白: 画面端から 12pt（ホームのカード）・16pt（設定）
- spacing scale: 4, 8, 12, 16, 20, 24, 32
- 角丸: ヒーローカード 28 / リストカード 24 / 設定グループ 20 / 主ボタン 全丸（高さ 52）/ チップ 全丸（高さ 36）/ Add ピル 全丸（高さ 30）
- 主要寸法: 上部の円形ボタン 44pt / セグメント 180×44pt / ヒーローカード 高さ 200pt / Acti 101 カード 高さ 64pt / リスト行 高さ 70pt / 行のアイコン 48pt / 下部の入力バー 高さ 52pt
- 影: 暗い背景のため影はほぼ無し。カードの縁に 1pt の白 6〜8% の線（ガラス調のハイライト）

## Motion
| トークン | duration | easing | 用途 |
|---|---|---|---|
| motion.press | 100ms | easeOut | ボタンを押したときの縮小（0.97） |
| motion.segment | 250ms | spring（応答 0.3、減衰 0.85） | All/My の選択ピル |
| motion.sheet | iOS 標準 | iOS 標準 | 設定シート・PiP ガイド |
| motion.push | iOS 標準 | iOS 標準 | 設定の階層 |
| motion.wheelOpen | 250ms | spring | Wheel が下から拡大して出る |
| motion.thinkingDots | 1.2s ループ | easeInOut | 「Thinking •••」のドット |
| motion.orbPulse | 1.5s ループ | easeInOut | 読み込み中のオーブ |
| motion.coachmark | 300ms | easeOut | コーチマークのフェードイン + 手のアイコンのタップ動作 |

## Components
| コンポーネント | バリアント / 状態 | 備考 | 根拠 |
|---|---|---|---|
| CircleIconButton | default / pressed / highlighted（コーチマーク時は白い輪） | 44pt、surface.raised、半透明 | ホーム・設定 |
| SegmentedPill | All / My、サブ: All / Added / Created | 選択ピルがスライド | ホーム |
| HeroCard | — | 明るいガラス調、キーボードのイラスト + オーブ + タイトル | ホーム |
| PromoRow（Acti 101） | New バッジ付き | 左にイラスト、右に再生ボタン（白丸） | ホーム |
| CategoryChip | default / selected | 横スクロール | ホーム |
| ActListCard | 3行 / 区切り線 | 横ページング | ホーム |
| ActRow | Add / Added | アイコン・名前・uses・Add ピル | ホーム・一覧 |
| PrimaryButton | default / pressed / disabled（半透明） | 全丸の白 | 各所 |
| SecondaryButton | Remix / Add（アイコン + 文字） | surface.raised | Act 詳細 |
| ComposerBar | 「Describe an Act to build...」 | ✦ アイコン、下部固定、ガラス調 | ホーム |
| SettingsGroup / SettingsRow | chevron / toggle / value + chevron / popup | アイコンは線画 | 設定 |
| AccountCard | ゲスト / サインイン済み（未確認） | クレジット表示と UPGRADE | 設定 |
| CreditBar | 残量 / 0（未確認） | 白の進捗バー | 設定 |
| LabeledSlider | 3〜4段階（ラベル付き） | Key Label Weight・Haptic・Polishing | 設定 |
| ProFeatureHero | ACTI Pro バッジ + EXCLUSIVE FEATURE | 図解カード + 2ステップの説明 + 主ボタン | Phone Mic・Desktop Clips |
| WheelSlots | 5スロット（中央 Acti AI、他は + ） | 長押しで並び替え / 削除 | Wheel 設定 |
| Coachmark | ツールチップ + 手のアイコン | 背景を暗くする | デモ・ホーム |
| ThinkingPanel | Thinking / 結果 | キーボードの位置に出る | デモ |
| OrbIcon | 静止 / パルス | Acti AI の象徴 | 各所 |

## ShogunAI 版での変更（2026-11-09 改訂）
1回目はガラスを全体に掛けすぎて録画から離れたため、録画の見た目に戻したうえで、ガラスは iOS 26 の考え方どおりの場所だけに絞った。

- **録画どおりに戻したもの**: 背景（フラットな `#11161D`）、カード・設定グループ（不透明の `#242A31`）、主ボタン（白の不透明）、キーボード（`#222222` / キー `#414141`）、Add の青 `#5094D7`
- **文字サイズ（録画から再計測）**: セクション見出し 18pt Medium、Act 名 16pt Medium、チップ 14pt、設定の行 16pt Regular（行の高さ 64pt）、詳細のタイトル 19pt
- **リキッドグラスを使う場所**: スクロールする内容の上に浮く操作部品だけ
  - ホーム上部の丸ボタン・All/My・検索、下部の「Describe an Act to build...」バー
  - Act 詳細の戻る / その他のボタンと、下に浮く Remix / Add（Try It は白の不透明）
  - 設定などのナビゲーションバーのボタンは、iOS 26 ではシステムが自動でガラスにする（二重にしない）
  - 内容は浮くバーの下をスクロールし、iOS 26 はスクロールエッジ効果（`.soft`）で柔らかくぼける。それ以前はフェードで近似
- **使わない場所**: カード、リスト、設定のグループ、チップ、Add（すべて内容レイヤー）
- **Act アイコン**: 録画の立体的なパステル調に近づけるため、白から淡い色への球 + 絵文字（`ActIconStyle`）
- **設定アイコン**: 録画と同じ白線 + 青の2色（SF Symbols の palette）
- **ブランド**: ロゴの青 `#034BFF` はロゴとアプリアイコンにだけ使う
