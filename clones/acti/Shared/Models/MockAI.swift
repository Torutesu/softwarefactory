import Foundation

// 本家はサーバー経由で LLM (Gemini) を呼ぶ。再現ではネットワークを使わない決定的なモックで応答する。
// 実 LLM に差し替える場合は AIService を実装して MockAI の代わりに注入する。

protocol AIService: Sendable {
    func run(_ act: ActItem, input: String) async throws -> String
    func transcribe(polishing: Int, emoji: Bool, translateTo: String?) async throws -> String
    func agentReply(to message: String, draft: ActItem?) async throws -> AgentReply
}

struct AgentReply: Sendable {
    var text: String
    var proposal: ActItem?
}

struct MockAI: AIService {
    var latency: Duration = .milliseconds(1200)

    func run(_ act: ActItem, input: String) async throws -> String {
        try await Task.sleep(for: latency)
        return Self.respond(act, input: input)
    }

    func transcribe(polishing: Int, emoji: Bool, translateTo: String?) async throws -> String {
        try await Task.sleep(for: .milliseconds(900))
        let raw = "um so I think we should uh meet around six tomorrow if that works for you"
        var text: String
        switch polishing {
        case 0: text = raw
        case 1: text = "So I think we should meet around six tomorrow, if that works for you."
        default: text = "Shall we meet around 6 PM tomorrow? Let me know if that works for you."
        }
        if let lang = translateTo, lang == "Japanese" {
            text = "明日の18時ごろに会いませんか？都合がよければ教えてください。"
        }
        if emoji { text += " 🙂" }
        return text
    }

    func agentReply(to message: String, draft: ActItem?) async throws -> AgentReply {
        try await Task.sleep(for: .milliseconds(1400))
        let lower = message.lowercased()
        if var d = draft {
            if lower.contains("clipboard") { d.input = .clipboard }
            if lower.contains("append") || lower.contains("after") { d.output = .append }
            if lower.contains("copy") { d.output = .copy }
            if lower.contains("short") { d.prompt += " Keep it short." }
            d.overview = d.prompt
            return AgentReply(text: "Updated the plan. Test it below, then save when it looks right.", proposal: d)
        }
        let name = Self.title(from: message)
        let act = ActItem(
            id: "custom-\(UUID().uuidString.prefix(8))",
            name: name,
            summary: message,
            overview: message,
            category: .writing, uses: 0, symbol: "wand.and.stars", colors: [0xD0BFFF, 0x7048E8],
            author: "You", authorActs: 1,
            input: lower.contains("clipboard") ? .clipboard : .textEntered,
            output: lower.contains("append") ? .append : .replace,
            kind: .custom, prompt: message,
            tools: lower.contains("calendar") || lower.contains("meet") ? ["Calendar"] : []
        )
        return AgentReply(text: "Here's a plan for your Act. It reads the input, applies your instruction and delivers the result as shown.", proposal: act)
    }

    private static func title(from message: String) -> String {
        let words = message.split(separator: " ").prefix(4).map { $0.capitalized }
        return words.isEmpty ? "My Act" : words.joined(separator: " ")
    }

    static func respond(_ act: ActItem, input raw: String) -> String {
        let input = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = input.lowercased()
        switch act.kind {
        case .askAI:
            if lower.contains("movie") {
                return "🎬 New movies to check out\n1. Night Signal (2026)\n2. The Last Orchard\n3. Paper Moons\nTrailers are in the app of your choice."
            }
            if lower.contains("cinema") || lower.contains("theater") || lower.contains("nearby") {
                return "📍 Starlight Cinema 12 — 0.8 mi, open until midnight\n📍 Harbor Screens — 1.4 mi"
            }
            if lower.contains("weather") { return "☀️ 72°F and clear today, light breeze in the evening." }
            return input.isEmpty ? "Ask me anything — type a question first." : "Here's a quick answer about “\(input)”: it depends on the details, but the short version is yes — and here's why it matters."
        case .translate:
            return input.isEmpty ? "" : "（翻訳）\(input)"
        case .decode:
            return "It means: “\(input)” — a friendly message asking how you are."
        case .restyle:
            return "✨ " + input.capitalizedSentence + " — said with a little more flair."
        case .polish:
            return input.capitalizedSentence.endingWithPeriod
        case .native:
            return input.isEmpty ? "" : "Honestly, " + input.lowercasedFirst.endingWithPeriod
        case .post:
            return "\(input.capitalizedSentence.endingWithPeriod)\n\nShipping beats perfect. 🚀"
        case .prompt:
            return "Role: expert assistant\nTask: \(input)\nFormat: 3 bullet points, plain language\nConstraints: be specific, cite assumptions"
        case .explain:
            return "In simple words: \(input.lowercasedFirst) — basically, it's about doing one thing well."
        case .keyPoints:
            let parts = input.split(whereSeparator: { ".!?".contains($0) }).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            return parts.prefix(3).map { "• \($0)" }.joined(separator: "\n").ifEmpty("• Nothing to summarize yet")
        case .warmReply:
            return "That sounds great! Thanks for thinking of me 😊"
        case .charmReply:
            return "Only if you promise to pick the snacks 😉"
        case .officeReply:
            return "Thanks for the update. I'll review it and get back to you by end of day."
        case .meetLink:
            return "Join the video call: https://meet.example.com/abc-defg-hij"
        case .languageBuddy:
            return "\(input.capitalizedSentence.endingWithPeriod)\n(Tip: capitalize the first word and end with a period.)"
        case .saveNote:
            return "Saved to Notes ✓"
        case .emojify:
            return input + " 🎉✨🙌"
        case .lyrics:
            return "🎵 \(input.capitalizedSentence)\nOh-oh, all night long 🎵"
        case .fortune:
            return "🔮 Today favors bold moves. Lucky color: teal."
        case .market:
            return "📈 Big tech mixed today (demo data): +0.8%, −0.3%, +1.2%."
        case .coin:
            return "🪙 Major coins (demo data): BTC +1.1%, ETH −0.4%."
        case .stockBrief:
            return "\(input.uppercased().ifEmpty("TICKER")): steady week, up 2.1% (demo data)."
        case .trip:
            return "🗺️ One day in \(input.ifEmpty("the city")): morning market → museum → sunset viewpoint → local dinner."
        case .localEats:
            return "🍜 Noodle Corner (0.3 mi) · 🌮 Taco Lane (0.6 mi)"
        case .packing:
            return "🧳 Passport · Charger · Layers · Toiletries · Snacks"
        case .custom:
            return "\(act.name): \(input.isEmpty ? "(no input)" : input) → done ✓"
        }
    }
}

private extension String {
    var capitalizedSentence: String {
        guard let first = first else { return self }
        return first.uppercased() + dropFirst()
    }
    var lowercasedFirst: String {
        guard let first = first else { return self }
        return first.lowercased() + dropFirst()
    }
    var endingWithPeriod: String {
        guard let last = last else { return self }
        return ".!?".contains(last) ? self : self + "."
    }
    func ifEmpty(_ fallback: String) -> String { isEmpty ? fallback : self }
}
