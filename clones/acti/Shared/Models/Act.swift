import Foundation

// Act = キーボードから呼び出す再利用可能な AI ワークフロー。
// 名前・説明文は本家の文言を使わず、ダミーに置き換えている (assets-todo.md 参照)。

enum ActCategory: String, Codable, CaseIterable, Identifiable, Hashable {
    case translate, writing, understand, reply, work, fun, finance, travel

    var id: String { rawValue }

    var title: String {
        switch self {
        case .translate: "Translate"
        case .writing: "Writing"
        case .understand: "Understand"
        case .reply: "Reply"
        case .work: "Work"
        case .fun: "Fun"
        case .finance: "Finance"
        case .travel: "Travel"
        }
    }
}

enum ActInput: String, Codable, CaseIterable, Hashable {
    case textEntered, clipboard

    var title: String {
        switch self {
        case .textEntered: "Text Entered"
        case .clipboard: "Clipboard"
        }
    }

    var symbol: String {
        switch self {
        case .textEntered: "character.cursor.ibeam"
        case .clipboard: "doc.on.clipboard"
        }
    }
}

enum ActOutput: String, Codable, CaseIterable, Hashable {
    case replace, append, copy

    var title: String {
        switch self {
        case .replace: "In-Place Replace"
        case .append: "Append After"
        case .copy: "Copy to Clipboard"
        }
    }

    var symbol: String {
        switch self {
        case .replace: "arrow.triangle.2.circlepath"
        case .append: "text.append"
        case .copy: "doc.on.doc"
        }
    }
}

/// モック AI の振る舞いを決める種類
enum ActKind: String, Codable, Hashable {
    case askAI, translate, restyle, polish, native, post, prompt
    case explain, keyPoints, decode
    case warmReply, charmReply, officeReply
    case meetLink, languageBuddy, saveNote
    case emojify, lyrics, fortune
    case market, coin, stockBrief
    case trip, localEats, packing
    case custom
}

struct ActItem: Identifiable, Codable, Hashable {
    var id: String
    var name: String
    var summary: String
    var overview: String
    var category: ActCategory
    var uses: Int
    var symbol: String
    var colors: [UInt32]
    var author: String
    var authorActs: Int
    var isOfficial: Bool = false
    var input: ActInput = .textEntered
    var output: ActOutput = .replace
    var kind: ActKind
    /// custom の Act だけが使う指示文 (Agent が作る)
    var prompt: String = ""
    var tools: [String] = []
    var isPublished: Bool = false

    var usesLabel: String {
        if uses >= 1000 {
            let k = Double(uses) / 1000
            return String(format: k >= 100 ? "%.0fK uses" : "%.1fK uses", k)
        }
        return "\(uses) uses"
    }
}

enum ActCatalog {
    static let aiID = "shogunai-ai"

    static let ai = ActItem(
        id: aiID, name: "Shogun AI",
        summary: "Ask anything right where you type.",
        overview: "Write a question or a request in the current chat and trigger Shogun AI. It looks things up and puts the answer straight into the text field — no app switching, no copy and paste.",
        category: .writing, uses: 0, symbol: "sparkles", colors: [0x6FD3FF, 0x6A7BFF],
        author: "ShogunAI Official", authorActs: 1, isOfficial: true,
        input: .textEntered, output: .replace, kind: .askAI
    )

    private static func act(
        _ id: String, _ name: String, _ summary: String, _ category: ActCategory, _ uses: Int,
        _ symbol: String, _ colors: [UInt32], _ kind: ActKind,
        author: String = "ShogunAI Labs", authorActs: Int = 24,
        input: ActInput = .textEntered, output: ActOutput = .replace, tools: [String] = []
    ) -> ActItem {
        ActItem(
            id: id, name: name, summary: summary,
            overview: summary + " Works on the text in the current field and keeps your original intent, tone and formatting.",
            category: category, uses: uses, symbol: symbol, colors: colors,
            author: author, authorActs: authorActs, input: input, output: output, kind: kind, tools: tools
        )
    }

    static let community: [ActItem] = [
        act("translate-any", "Translate Anything", "Detects the source language and translates into the one you pick.", .writing, 11_800, "globe", [0x9BD3FF, 0x4C7DFF], .translate),
        act("restyle", "Restyle Text", "Rewrites any text in the style you choose.", .writing, 9_900, "paintbrush.pointed", [0xFFD6A5, 0xB28DFF], .restyle),
        act("polish", "Polish Grammar", "Fixes grammar, spelling and punctuation.", .writing, 8_700, "checkmark.seal", [0xC9E7FF, 0x8FB8FF], .polish),
        act("native", "Sound Natural", "Turns stiff or literal text into how a native speaker would say it.", .writing, 10_300, "bubble.left.and.text.bubble.right", [0xB8F2C8, 0x5CC98A], .native, author: "Mira Notes", authorActs: 11),
        act("post", "Draft a Post", "Crafts a punchy, authentic short social post.", .writing, 5_500, "square.and.pencil", [0xFFC2C2, 0x5C8DFF], .post),
        act("prompt", "Sharpen Prompt", "Refines vague prompts into clear, structured ones.", .writing, 8_200, "slider.horizontal.3", [0xB6C8FF, 0x3E5BD6], .prompt),
        act("decode", "Decode Any Language", "Explains what a message in another language means.", .understand, 8_000, "character.book.closed", [0xA7F3E8, 0x4CB8D9], .decode),
        act("explain", "Explain Simply", "Explains complex text in plain words.", .understand, 8_300, "lightbulb", [0xD3F8B5, 0x5CC9A0], .explain),
        act("keypoints", "Key Points", "Pulls out what matters from long text.", .understand, 9_700, "list.bullet.rectangle", [0xFFB4A2, 0xE5484D], .keyPoints),
        act("warm-reply", "Warm Reply", "Drafts a friendly, human-sounding reply.", .reply, 6_600, "face.smiling", [0xC5D9FF, 0x7C9CFF], .warmReply),
        act("charm-reply", "Charm Reply", "Suggests a playful, charming reply.", .reply, 8_500, "heart", [0xFFB3C1, 0xFF5C7A], .charmReply),
        act("office-reply", "Office Reply", "Turns a quick note into a professional reply.", .reply, 6_000, "briefcase", [0xFFE7A3, 0xF0B429], .officeReply),
        act("meet", "Instant Meet Link", "Creates a video meeting link and drops it in the chat.", .work, 5_500, "video", [0xB5E3FF, 0x4C9BFF], .meetLink, output: .append, tools: ["Calendar"]),
        act("language-buddy", "Language Buddy", "Corrects your sentence and explains the fix.", .work, 7_300, "graduationcap", [0xFFD1B3, 0xE2725B], .languageBuddy),
        act("save-note", "Send to Notes", "Saves the current text to your notes workspace.", .work, 6_200, "tray.and.arrow.down", [0xC8D7FF, 0x5C7CFA], .saveNote, output: .copy, tools: ["Notes"]),
        act("emojify", "Emojify", "Adds fitting emoji to your text.", .fun, 6_100, "face.dashed", [0xFFF0A8, 0xF5B700], .emojify),
        act("lyrics", "Song Lyrics", "Turns your text into short song lyrics.", .fun, 9_600, "music.note", [0xE0E4EA, 0x8A94A6], .lyrics),
        act("fortune", "Daily Luck", "A playful fortune for your day.", .fun, 9_700, "moon.stars", [0xD6C8FF, 0x5B3FD6], .fortune),
        act("market", "Market Pulse", "Summarizes how the big tech names are doing (demo data).", .finance, 8_700, "chart.bar", [0xC9F5C9, 0x3DBE6B], .market),
        act("coin", "Coin Watch", "Quick snapshot of major coins (demo data).", .finance, 9_600, "bitcoinsign.circle", [0xFFD8A8, 0xF08C00], .coin),
        act("stock", "Stock Brief", "A short brief on the ticker you type (demo data).", .finance, 5_700, "chart.line.uptrend.xyaxis", [0xC3FAE8, 0x20C997], .stockBrief),
        act("trip", "Trip Planner", "Builds a one-day plan for the place you type.", .travel, 4_900, "airplane", [0xBDE0FE, 0x3A86FF], .trip),
        act("eats", "Local Eats", "Suggests places to eat nearby (demo data).", .travel, 4_100, "fork.knife", [0xFFD6E0, 0xFF6B6B], .localEats),
        act("packing", "Packing List", "Makes a packing list for your trip.", .travel, 3_800, "suitcase", [0xE9D8FD, 0x9775FA], .packing),
    ]

    static let builtIn: [ActItem] = [ai] + community

    static func byCategory(_ category: ActCategory) -> [ActItem] {
        if category == .translate {
            return community.filter { [.translate, .decode, .languageBuddy].contains($0.kind) }
        }
        return community.filter { $0.category == category }
    }

    /// ホームのチップの並び。Translate は Writing の翻訳系に寄せる (録画ではチップのみ確認)
    static let homeCategories: [ActCategory] = ActCategory.allCases
}
