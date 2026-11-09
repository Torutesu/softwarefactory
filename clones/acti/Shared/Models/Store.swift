import Foundation
import Observation

// アプリ本体とキーボード拡張で共有する状態。App Group の UserDefaults に JSON で保存する。

enum AppGroup {
    static let id = "group.com.example.shogunai"
    static var defaults: UserDefaults { UserDefaults(suiteName: id) ?? .standard }
}

struct KeyboardPrefs: Codable, Equatable {
    var keyLabelWeight = 1          // 0 Thin / 1 Default / 2 Regular / 3 SemiBold
    var alwaysUppercase = false
    var characterPreview = true
    var haptics = false
    var hapticStrength = 1          // 0 Low / 1 Medium / 2 High
    var keySounds = true
    var autoCapitalization = true
    var periodShortcut = true
    var predictiveText = true
    var nextWordSuggestions = true
    var slideToType = true
    var swipeUpForNumbers = true
    var spaceCursorControl = true
    var backspaceAcceleration = true
    var inlineComposition = true
    var emojiKey = true
    var commaKey = false
    var keyboardHeight = 1          // 0 Compact / 1 Default / 2 Tall
}

struct VoicePrefs: Codable, Equatable {
    var spokenLanguages: [String] = []      // 空 = Auto-detect
    var polishing = 2                        // 0 Raw / 1 Clean / 2 Smart
    var emoji = false
    var translateTargets: [String] = ["English", "Japanese"]
    var enhancedCommand = false
}

enum AppearanceMode: String, Codable, CaseIterable, Identifiable {
    case automatic, light, dark
    var id: String { rawValue }
    var title: String {
        switch self {
        case .automatic: "Automatic"
        case .light: "Light"
        case .dark: "Dark"
        }
    }
}

struct RunRecord: Codable, Identifiable, Hashable {
    var id = UUID()
    var actID: String
    var actName: String
    var input: String
    var output: String
    var date: Date
}

struct Snippet: Codable, Identifiable, Hashable {
    var id = UUID()
    var text: String
    var abbreviation = ""
    var category = "General"
    var pinned = false
}

struct PersistedState: Codable {
    var onboardingDone = false
    var seenSettingsCoachmark = false
    var addedActIDs: [String] = [ActCatalog.aiID]
    var createdActs: [ActItem] = []
    var wheelSlots: [String?] = [nil, nil, ActCatalog.aiID, nil, nil]
    var keyAssignments: [String: String] = [:]     // "t" -> actID
    var keyboard = KeyboardPrefs()
    var voice = VoicePrefs()
    var appearance: AppearanceMode = .automatic
    var languages: [String] = ["English"]
    var creditsUsed = 0
    var creditsDay = ""
    var history: [RunRecord] = []
    var clips: [String] = []
    var snippets: [Snippet] = []
    var connectedTools: [String] = []
    var followedAuthors: [String] = []
    var keyboardEverLoaded = false
    var keyboardFullAccess = false
}

@MainActor
@Observable
final class AppStore {
    static let shared = AppStore()

    static let dailyCredits = 10
    private static let key = "shogunai.state.v1"

    var state: PersistedState {
        didSet { save() }
    }

    init(defaults: UserDefaults = AppGroup.defaults) {
        self.defaults = defaults
        state = Self.load(from: defaults)
    }

    @ObservationIgnored private let defaults: UserDefaults

    private static func load(from defaults: UserDefaults) -> PersistedState {
        guard let data = defaults.data(forKey: key),
              let decoded = try? JSONDecoder().decode(PersistedState.self, from: data)
        else { return PersistedState() }
        return decoded
    }

    /// 他プロセス (キーボード / アプリ) が書いた内容を読み直す
    func reload() {
        state = Self.load(from: defaults)
    }

    private func save() {
        if let data = try? JSONEncoder().encode(state) {
            defaults.set(data, forKey: Self.key)
        }
    }

    func resetGuestData() {
        state = PersistedState()
    }

    // MARK: Acts

    var allActs: [ActItem] { ActCatalog.builtIn + state.createdActs }

    func act(_ id: String?) -> ActItem? {
        guard let id else { return nil }
        return allActs.first { $0.id == id }
    }

    var addedActs: [ActItem] { state.addedActIDs.compactMap { act($0) } }

    func isAdded(_ act: ActItem) -> Bool { state.addedActIDs.contains(act.id) }

    func toggleAdded(_ act: ActItem) {
        if let i = state.addedActIDs.firstIndex(of: act.id) {
            guard act.id != ActCatalog.aiID else { return }   // Shogun AI は外せない
            state.addedActIDs.remove(at: i)
            for (slot, id) in state.wheelSlots.enumerated() where id == act.id && slot != 2 {
                state.wheelSlots[slot] = nil
            }
            state.keyAssignments = state.keyAssignments.filter { $0.value != act.id }
        } else {
            state.addedActIDs.append(act.id)
        }
    }

    func saveCreated(_ act: ActItem) {
        if let i = state.createdActs.firstIndex(where: { $0.id == act.id }) {
            state.createdActs[i] = act
        } else {
            state.createdActs.insert(act, at: 0)
        }
        if !state.addedActIDs.contains(act.id) { state.addedActIDs.append(act.id) }
    }

    func deleteCreated(_ act: ActItem) {
        state.createdActs.removeAll { $0.id == act.id }
        state.addedActIDs.removeAll { $0 == act.id }
        state.keyAssignments = state.keyAssignments.filter { $0.value != act.id }
        state.wheelSlots = state.wheelSlots.map { $0 == act.id ? nil : $0 }
    }

    func assign(_ act: ActItem, toKey letter: String?) {
        state.keyAssignments = state.keyAssignments.filter { $0.value != act.id }
        if let letter { state.keyAssignments[letter.lowercased()] = act.id }
        if !isAdded(act) { state.addedActIDs.append(act.id) }
    }

    func assignedKey(for act: ActItem) -> String? {
        state.keyAssignments.first { $0.value == act.id }?.key
    }

    // MARK: Credits

    private static var today: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }

    var creditsRemaining: Int {
        let used = state.creditsDay == Self.today ? state.creditsUsed : 0
        return max(0, Self.dailyCredits - used)
    }

    /// AI を使う操作の前に呼ぶ。残りが無ければ false
    func spendCredit() -> Bool {
        if state.creditsDay != Self.today {
            state.creditsDay = Self.today
            state.creditsUsed = 0
        }
        guard state.creditsUsed < Self.dailyCredits else { return false }
        state.creditsUsed += 1
        return true
    }

    // MARK: History / Clips

    func record(_ act: ActItem, input: String, output: String) {
        state.history.insert(RunRecord(actID: act.id, actName: act.name, input: input, output: output, date: Date()), at: 0)
        if state.history.count > 100 { state.history.removeLast(state.history.count - 100) }
    }

    func addClip(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        state.clips.removeAll { $0 == t }
        state.clips.insert(t, at: 0)
        if state.clips.count > 50 { state.clips.removeLast(state.clips.count - 50) }
    }
}
