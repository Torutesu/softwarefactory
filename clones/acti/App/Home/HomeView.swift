import SwiftUI

// S10〜S12: ホーム (Acts ギャラリー)。録画 87–158s。

enum HomeTab: String, CaseIterable, Hashable { case all = "All", my = "My" }

enum HomeRoute: Hashable {
    case act(ActItem)
    case category(ActCategory)
    case agent(String?, ActItem?)
    case search
}

@MainActor
struct HomeView: View {
    @Environment(AppStore.self) private var store
    @State private var tab: HomeTab = .all
    @State private var path: [HomeRoute] = []
    @State private var showSettings = false
    @State private var showTips = false

    var body: some View {
        NavigationStack(path: $path) {
            ZStack(alignment: .bottom) {
                VStack(spacing: 0) {
                    header
                    Group {
                        switch tab {
                        case .all: AllActsView(path: $path, showTips: $showTips)
                        case .my: MyActsView(path: $path)
                        }
                    }
                    .frame(maxHeight: .infinity)
                }
                ComposerBar { path.append(.agent(nil, nil)) }
                    .padding(.horizontal, KA.S.screenH)
                    .padding(.bottom, 4)
            }
            .screenBackground()
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: HomeRoute.self) { route in
                switch route {
                case .act(let act): ActDetailView(act: act, path: $path)
                case .category(let c): CategoryListView(category: c, path: $path)
                case .agent(let prompt, let base): AgentChatView(initialPrompt: prompt, remixOf: base)
                case .search: SearchView(path: $path)
                }
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showTips) { TipsView() }
    }

    private var header: some View {
        HStack {
            CircleIconButton(systemName: "gearshape", highlighted: !store.state.seenSettingsCoachmark) {
                store.state.seenSettingsCoachmark = true
                showSettings = true
            }
            .overlay(alignment: .bottomLeading) {
                if !store.state.seenSettingsCoachmark {
                    VStack(alignment: .leading, spacing: 4) {
                        TapHand().offset(x: 10)
                        CoachmarkBubble(text: "More settings here")
                    }
                    .fixedSize()
                    .offset(y: 86)
                    .allowsHitTesting(false)
                }
            }
            .zIndex(1)
            Spacer()
            SegmentedPill(options: HomeTab.allCases, selection: $tab) { $0.rawValue }
            Spacer()
            CircleIconButton(systemName: "magnifyingglass") { path.append(.search) }
        }
        .padding(.horizontal, KA.S.screenH)
        .padding(.top, 4)
        .padding(.bottom, 8)
        .zIndex(1)
    }
}

// MARK: - All

@MainActor
private struct AllActsView: View {
    @Binding var path: [HomeRoute]
    @Binding var showTips: Bool
    @State private var selectedChip: ActCategory?

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Button { path.append(.act(ActCatalog.ai)) } label: { HeroCard() }
                        .buttonStyle(.pressable)
                        .padding(.horizontal, KA.S.screenH)

                    PromoRow { showTips = true }
                        .padding(.horizontal, KA.S.screenH)
                        .padding(.top, 16)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(ActCatalog.homeCategories) { c in
                                Chip(title: c.title, selected: selectedChip == c) {
                                    selectedChip = c
                                    withAnimation { proxy.scrollTo(c, anchor: .top) }
                                }
                            }
                        }
                        .padding(.horizontal, KA.S.screenH)
                    }
                    .padding(.top, 20)

                    ForEach(ActCatalog.homeCategories.filter { $0 != .translate }) { c in
                        CategorySection(category: c, path: $path)
                            .id(c)
                            .padding(.top, 28)
                    }
                    Color.clear.frame(height: 90)
                }
            }
        }
    }
}

@MainActor
struct HeroCard: View {
    var body: some View {
        ZStack {
            LinearGradient(colors: [KA.C.heroTop, KA.C.heroBottom], startPoint: .top, endPoint: .bottom)
            // キーボードのイラスト (ぼかし)
            VStack(spacing: 6) {
                HStack(spacing: 10) {
                    Label("Type", systemImage: "pencil")
                    Label("Act", systemImage: "sparkles").opacity(0.5)
                    Spacer()
                    Image(systemName: "doc.on.clipboard")
                    Label("Voice", systemImage: "mic.fill")
                }
                .font(.system(size: 10, weight: .medium))
                .padding(.horizontal, 30)
                HStack(spacing: 6) {
                    ForEach(Array("qwertyuiop"), id: \.self) { ch in
                        Text(String(ch))
                            .font(.system(size: 14, design: .rounded))
                            .frame(width: 22, height: 26)
                            .background(RoundedRectangle(cornerRadius: 6).fill(.white.opacity(0.75)))
                    }
                }
                HStack(spacing: 6) {
                    ForEach(0..<9, id: \.self) { _ in
                        RoundedRectangle(cornerRadius: 6).fill(.white.opacity(0.5)).frame(width: 22, height: 26)
                    }
                }
                .blur(radius: 3)
            }
            .foregroundStyle(Color(hex: 0x4A5568))
            .rotation3DEffect(.degrees(28), axis: (x: 1, y: 0, z: 0), perspective: 0.6)
            .offset(y: 26)

            VStack(spacing: 12) {
                OrbIcon(size: 72)
                Text("Keyact AI").font(KA.F.hero).foregroundStyle(Color(hex: 0x11161D))
            }
            .offset(y: -6)

            VStack {
                HStack { BrandMark(size: 26).colorInvert().opacity(0.6); Spacer() }
                Spacer()
            }
            .padding(14)
        }
        .frame(height: KA.Size.heroHeight)
        .clipShape(RoundedRectangle(cornerRadius: KA.R.hero, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: KA.R.hero, style: .continuous).stroke(.white.opacity(0.5), lineWidth: 1))
    }
}

@MainActor
struct PromoRow: View {
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                ZStack {
                    Image(systemName: "keyboard").font(.system(size: 22)).foregroundStyle(Color(hex: 0x5A7AA8)).offset(x: -10, y: 6)
                    Image(systemName: "lightbulb.fill")
                        .font(.system(size: 26))
                        .foregroundStyle(LinearGradient(colors: [Color(hex: 0xFFF3B0), Color(hex: 0xF5C542)], startPoint: .top, endPoint: .bottom))
                        .shadow(color: Color(hex: 0xF5C542).opacity(0.6), radius: 8)
                        .offset(x: 6, y: -4)
                }
                .frame(width: 60)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text("Keyact 101").font(KA.F.headline).foregroundStyle(KA.C.text)
                        Badge(text: "New", style: .new)
                    }
                    Text("Tips for using the Keyact keyboard").font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
                Spacer()
                Image(systemName: "play.fill")
                    .font(.system(size: 13))
                    .foregroundStyle(Color(hex: 0x11161D))
                    .frame(width: 34, height: 34)
                    .background(Circle().fill(.white))
            }
            .padding(.trailing, 14)
            .frame(height: KA.Size.promoHeight + 8)
            .card(radius: KA.R.group, fill: Color(hex: 0x1A2834))
        }
        .buttonStyle(.pressable)
    }
}

/// カテゴリ見出し + 3 行カードの横ページング
@MainActor
struct CategorySection: View {
    var category: ActCategory
    @Binding var path: [HomeRoute]

    var body: some View {
        let acts = ActCatalog.byCategory(category)
        let pages = stride(from: 0, to: acts.count, by: 3).map { Array(acts[$0..<min($0 + 3, acts.count)]) }
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(category.title).font(KA.F.title).foregroundStyle(KA.C.text)
                Spacer()
                Button { path.append(.category(category)) } label: {
                    HStack(spacing: 4) {
                        Text("More")
                        Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold))
                    }
                    .font(KA.F.body)
                    .foregroundStyle(KA.C.textSecondary)
                }
            }
            .padding(.horizontal, KA.S.screenH + 6)

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 10) {
                    ForEach(Array(pages.enumerated()), id: \.offset) { _, page in
                        VStack(spacing: 0) {
                            ForEach(Array(page.enumerated()), id: \.element.id) { i, act in
                                Button { path.append(.act(act)) } label: {
                                    ActRow(act: act, showsDivider: i < page.count - 1)
                                }
                                .buttonStyle(.plain)
                            }
                            if page.count < 3 { Spacer(minLength: 0) }
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 6)
                        .frame(height: KA.Size.rowHeight * 3 + 12, alignment: .top)
                        .card()
                        .containerRelativeFrame(.horizontal) { w, _ in w - KA.S.screenH * 2 - 24 }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .contentMargins(.horizontal, KA.S.screenH, for: .scrollContent)
        }
    }
}

@MainActor
struct ComposerBar: View {
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: "sparkles").font(.system(size: 17)).foregroundStyle(KA.C.text)
                Text("Describe an Act to build...").font(KA.F.body).foregroundStyle(KA.C.textSecondary)
                Spacer()
            }
            .padding(.horizontal, 18)
            .frame(height: KA.Size.composer)
            .background(.ultraThinMaterial, in: Capsule())
            .background(Capsule().fill(KA.C.cardAlt.opacity(0.8)))
            .overlay(Capsule().stroke(KA.C.hairline, lineWidth: 1))
        }
        .buttonStyle(.pressable)
    }
}

// MARK: - My

enum MyFilter: String, CaseIterable, Hashable { case all = "All", added = "Added", created = "Created" }

@MainActor
private struct MyActsView: View {
    @Environment(AppStore.self) private var store
    @Binding var path: [HomeRoute]
    @State private var filter: MyFilter = .all
    @State private var sortByName = false

    var body: some View {
        let acts = filtered
        VStack(spacing: 12) {
            HStack {
                SubTabs(options: MyFilter.allCases, selection: $filter) { $0.rawValue }
                Spacer()
                Button { sortByName.toggle() } label: {
                    Image(systemName: "arrow.up.arrow.down")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(sortByName ? KA.C.accent : KA.C.textSecondary)
                }
            }
            .padding(.horizontal, KA.S.screenH + 4)

            if acts.isEmpty {
                VStack(spacing: 10) {
                    Spacer()
                    Image(systemName: filter == .created ? "wand.and.stars" : "square.stack")
                        .font(.system(size: 34)).foregroundStyle(KA.C.textTertiary)
                    Text(filter == .created ? "Acts you build show up here" : "Add Acts from the All tab")
                        .font(KA.F.body).foregroundStyle(KA.C.textSecondary)
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(Array(acts.enumerated()), id: \.element.id) { i, act in
                            Button { path.append(.act(act)) } label: {
                                ActRow(
                                    act: act, showsDivider: i < acts.count - 1,
                                    trailing: AnyView(trailingInfo(act))
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 16)
                    .card()
                    .padding(.horizontal, KA.S.screenH)
                    Color.clear.frame(height: 90)
                }
            }
        }
    }

    private var filtered: [ActItem] {
        var list: [ActItem]
        switch filter {
        case .all: list = store.addedActs + store.state.createdActs.filter { !store.isAdded($0) }
        case .added: list = store.addedActs.filter { act in !store.state.createdActs.contains { $0.id == act.id } }
        case .created: list = store.state.createdActs
        }
        if sortByName { list.sort { $0.name < $1.name } }
        return list
    }

    @ViewBuilder
    private func trailingInfo(_ act: ActItem) -> some View {
        if let key = store.assignedKey(for: act) {
            Text(key.uppercased())
                .font(KA.F.caption.weight(.semibold))
                .foregroundStyle(KA.C.text)
                .frame(width: 26, height: 26)
                .background(RoundedRectangle(cornerRadius: 6).fill(KA.C.raised))
        }
        Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(KA.C.textTertiary)
    }
}

// MARK: - Category list / Search / Tips

@MainActor
struct CategoryListView: View {
    var category: ActCategory
    @Binding var path: [HomeRoute]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let acts = ActCatalog.byCategory(category)
        VStack(spacing: 0) {
            NavHeader(title: category.title, leading: AnyView(CircleIconButton(systemName: "chevron.left") { dismiss() }))
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(Array(acts.enumerated()), id: \.element.id) { i, act in
                        Button { path.append(.act(act)) } label: {
                            HStack(spacing: 14) {
                                ActIcon(act: act)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(act.name).font(KA.F.headline).foregroundStyle(KA.C.text)
                                    Text(act.summary).font(KA.F.caption).foregroundStyle(KA.C.textSecondary).lineLimit(1)
                                }
                                Spacer()
                            }
                            .padding(.vertical, 12)
                            .overlay(alignment: .bottom) {
                                if i < acts.count - 1 { Rectangle().fill(KA.C.divider).frame(height: 1).padding(.leading, 62) }
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, KA.S.screenHSettings)
            }
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
    }
}

@MainActor
struct SearchView: View {
    @Binding var path: [HomeRoute]
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @FocusState private var focused: Bool

    var body: some View {
        let results = query.isEmpty ? [] : store.allActs.filter {
            $0.name.localizedCaseInsensitiveContains(query) || $0.summary.localizedCaseInsensitiveContains(query)
        }
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                HStack {
                    Image(systemName: "magnifyingglass").foregroundStyle(KA.C.textSecondary)
                    TextField("Search Acts", text: $query)
                        .font(KA.F.body)
                        .foregroundStyle(KA.C.text)
                        .focused($focused)
                        .submitLabel(.search)
                    if !query.isEmpty {
                        Button { query = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(KA.C.textTertiary) }
                    }
                }
                .padding(.horizontal, 14)
                .frame(height: 44)
                .background(Capsule().fill(KA.C.card))
                Button("Cancel") { dismiss() }.font(KA.F.body).foregroundStyle(KA.C.text)
            }
            .padding(.horizontal, KA.S.screenH)
            .padding(.top, 8)

            if query.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Popular").font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
                    FlowChips(items: ["translate", "reply", "grammar", "emoji", "meeting"]) { query = $0 }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, KA.S.screenH + 4)
                Spacer()
            } else if results.isEmpty {
                Spacer()
                Text("No Acts found. Try building one!").font(KA.F.body).foregroundStyle(KA.C.textSecondary)
                Button("Build “\(query)”") { path.append(.agent(query, nil)) }
                    .font(KA.F.bodyMedium).foregroundStyle(KA.C.accent)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(results) { act in
                            Button { path.append(.act(act)) } label: { ActRow(act: act, showsDivider: true) }
                                .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 16)
                    .card()
                    .padding(.horizontal, KA.S.screenH)
                }
            }
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .onAppear { focused = true }
    }
}

@MainActor
private struct FlowChips: View {
    var items: [String]
    var onTap: (String) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(items, id: \.self) { item in
                    Chip(title: item) { onTap(item) }
                }
            }
        }
    }
}

@MainActor
struct TipsView: View {
    @Environment(\.dismiss) private var dismiss
    private let tips: [(String, String, String)] = [
        ("space", "Hold the space bar", "Hold space to open the Wheel, slide to an Act and release to run it."),
        ("arrow.left.and.right", "Swipe the space bar", "Swipe left or right on the space bar to move the cursor."),
        ("sparkles", "Run Acts from the Act tab", "Tap Act in the keyboard toolbar to see all of your Acts."),
        ("character.textbox", "Assign Acts to keys", "Open an Act, tap ••• › Assign to key, then long-press that key."),
        ("mic", "Speak first, decide later", "Tap Voice, talk, then choose Transcribe or Ask AI."),
    ]
    @State private var page = 0

    var body: some View {
        VStack(spacing: 20) {
            HStack {
                Spacer()
                CircleIconButton(systemName: "xmark") { dismiss() }
            }
            TabView(selection: $page) {
                ForEach(Array(tips.enumerated()), id: \.offset) { i, tip in
                    VStack(spacing: 18) {
                        Image(systemName: tip.0)
                            .font(.system(size: 44))
                            .foregroundStyle(KA.C.accent)
                            .frame(width: 120, height: 120)
                            .card(radius: 36)
                        Text(tip.1).font(KA.F.title).foregroundStyle(KA.C.text)
                        Text(tip.2).font(KA.F.body).foregroundStyle(KA.C.textSecondary).multilineTextAlignment(.center)
                    }
                    .padding(.horizontal, 30)
                    .tag(i)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .always))
            Button(page == tips.count - 1 ? "Got it" : "Next") {
                if page == tips.count - 1 { dismiss() } else { withAnimation { page += 1 } }
            }
            .buttonStyle(.primary)
        }
        .padding(KA.S.screenHSettings)
        .screenBackground()
    }
}
