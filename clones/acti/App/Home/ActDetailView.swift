import SwiftUI

// S14: Act 詳細 (録画 154–155s)。読み込み中は灰色のオーブを見せてからフェードイン。

@MainActor
struct ActDetailView: View {
    var act: ActItem
    @Binding var path: [HomeRoute]
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var loaded = false
    @State private var showTry = false
    @State private var showAssign = false
    @State private var confirmDelete = false

    private var current: ActItem { store.act(act.id) ?? act }
    private var isMine: Bool { store.state.createdActs.contains { $0.id == act.id } }

    var body: some View {
        VStack(spacing: 0) {
            NavHeader(
                title: "",
                leading: AnyView(CircleIconButton(systemName: "chevron.left") { dismiss() }),
                trailing: AnyView(moreMenu)
            )
            ZStack {
                if loaded {
                    content.transition(.opacity)
                } else {
                    VStack {
                        Spacer()
                        OrbIcon(size: 70, pulsing: true, grayscale: true)
                        Spacer()
                    }
                }
            }
            .frame(maxHeight: .infinity)
            bottomBar
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .task {
            try? await Task.sleep(for: .milliseconds(450))
            withAnimation(KA.M.fade) { loaded = true }
        }
        .sheet(isPresented: $showTry) { TryActSheet(act: current) }
        .sheet(isPresented: $showAssign) { AssignKeySheet(act: current) }
        .confirmationDialog("Delete this Act?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                store.deleteCreated(current)
                dismiss()
            }
        }
    }

    private var content: some View {
        ScrollView {
            VStack(spacing: 12) {
                Text(current.name).font(KA.F.title).foregroundStyle(KA.C.text).padding(.top, 4).padding(.bottom, 8)
                AuthorCard(
                    name: current.author, acts: current.authorActs, verified: current.isOfficial,
                    showsFollow: !current.isOfficial && current.author != "You",
                    following: store.state.followedAuthors.contains(current.author)
                ) {
                    if let i = store.state.followedAuthors.firstIndex(of: current.author) {
                        store.state.followedAuthors.remove(at: i)
                    } else {
                        store.state.followedAuthors.append(current.author)
                    }
                }
                OverviewCard(text: current.overview)
                InputOutputCard(act: current)
                if !current.tools.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Connected Apps").font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
                        ForEach(current.tools, id: \.self) { tool in
                            HStack {
                                Image(systemName: "link").foregroundStyle(KA.C.accent)
                                Text(tool).font(KA.F.body).foregroundStyle(KA.C.text)
                                Spacer()
                                Text(store.state.connectedTools.contains(tool) ? "Connected" : "Not connected")
                                    .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                            }
                        }
                    }
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .card(radius: KA.R.group, fill: KA.C.cardAlt)
                }
                if let key = store.assignedKey(for: current) {
                    Text("Long-press “\(key.uppercased())” on the keyboard to run this Act")
                        .font(KA.F.caption).foregroundStyle(KA.C.textSecondary)
                }
            }
            .padding(.horizontal, KA.S.screenH)
            .padding(.bottom, 20)
        }
    }

    private var bottomBar: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                Button { path.append(.agent(nil, current)) } label: { Label("Remix", systemImage: "shuffle") }
                    .buttonStyle(.secondary)
                Button { store.toggleAdded(current) } label: {
                    Label(store.isAdded(current) ? "Added" : "Add", systemImage: store.isAdded(current) ? "checkmark.circle" : "plus.circle")
                }
                .buttonStyle(.secondary)
                .disabled(current.id == ActCatalog.aiID)
            }
            Button("Try It") { showTry = true }
                .buttonStyle(.primary)
        }
        .padding(.horizontal, KA.S.screenH)
        .padding(.bottom, 8)
        .opacity(loaded ? 1 : 0.4)
        .disabled(!loaded)
    }

    private var moreMenu: some View {
        Menu {
            Button { showAssign = true } label: { Label("Assign to Key", systemImage: "keyboard") }
            ShareLink(item: "Check out the “\(current.name)” Act on Keyact") { Label("Share", systemImage: "square.and.arrow.up") }
            if isMine {
                Button {
                    var a = current
                    a.isPublished.toggle()
                    store.saveCreated(a)
                } label: {
                    Label(current.isPublished ? "Unpublish" : "Publish to Community", systemImage: current.isPublished ? "eye.slash" : "globe")
                }
                Button(role: .destructive) { confirmDelete = true } label: { Label("Delete", systemImage: "trash") }
            } else {
                Button(role: .destructive) {} label: { Label("Report", systemImage: "flag") }
            }
        } label: {
            Image(systemName: "ellipsis")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(KA.C.text)
                .frame(width: KA.Size.circleButton, height: KA.Size.circleButton)
                .background(Circle().fill(KA.C.raised.opacity(0.85)))
                .overlay(Circle().stroke(KA.C.hairline, lineWidth: 1))
        }
    }
}

@MainActor
struct InputOutputCard: View {
    var act: ActItem

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Input & Output").font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
            row(icon: act.input.symbol, title: act.input.title, tag: "Input")
            row(icon: act.output.symbol, title: act.output.title, tag: "Output")
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(radius: KA.R.group, fill: KA.C.cardAlt)
    }

    private func row(icon: String, title: String, tag: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 15))
                .foregroundStyle(KA.C.text)
                .frame(width: 36, height: 36)
                .background(RoundedRectangle(cornerRadius: 8).stroke(KA.C.hairline, lineWidth: 1))
            Text(title).font(KA.F.body).foregroundStyle(KA.C.text)
            Text(tag).font(KA.F.micro).foregroundStyle(KA.C.textSecondary)
            Spacer()
        }
    }
}

/// Try It: サンプル文に Act を実行して結果を見る
@MainActor
struct TryActSheet: View {
    var act: ActItem
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var input = ""
    @State private var output: String?
    @State private var running = false
    @State private var outOfCredits = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Input").font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
                TextField("Type something to try", text: $input, axis: .vertical)
                    .font(KA.F.body)
                    .foregroundStyle(KA.C.text)
                    .lineLimit(3...6)
                    .padding(14)
                    .card(radius: KA.R.inner, fill: KA.C.cardAlt)
                Button {
                    run()
                } label: {
                    if running { ProgressView().tint(KA.C.ctaText) } else { Label("Run", systemImage: "sparkles") }
                }
                .buttonStyle(.primary)
                .disabled(running)

                if outOfCredits {
                    Text("You've used today's \(AppStore.dailyCredits) AI credits. They reset daily.")
                        .font(KA.F.caption).foregroundStyle(KA.C.danger)
                }
                if let output {
                    Text("Result").font(KA.F.bodyMedium).foregroundStyle(KA.C.textSecondary)
                    Text(output)
                        .font(KA.F.body)
                        .foregroundStyle(KA.C.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(14)
                        .card(radius: KA.R.inner, fill: KA.C.cardAlt)
                        .transition(.opacity)
                }
                Spacer()
                Text("\(store.creditsRemaining) / \(AppStore.dailyCredits) AI credits left today")
                    .font(KA.F.caption).foregroundStyle(KA.C.textTertiary)
                    .frame(maxWidth: .infinity)
            }
            .padding(KA.S.screenHSettings)
            .screenBackground()
            .navigationTitle(act.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
            .onAppear { if input.isEmpty { input = sample } }
        }
        .presentationDetents([.large])
    }

    private var sample: String {
        switch act.kind {
        case .askAI: "Recent movies"
        case .translate, .decode: "Thanks so much for your help today!"
        case .keyPoints: "We shipped the beta. Feedback is positive. Next we fix onboarding. Launch is in May."
        case .stockBrief: "AAPL"
        case .trip, .packing: "Kyoto"
        default: "i think we should meet tomorrow to talk about the plan"
        }
    }

    private func run() {
        guard store.spendCredit() else { outOfCredits = true; return }
        running = true
        Task {
            let result = (try? await MockAI().run(act, input: input)) ?? ""
            store.record(act, input: input, output: result)
            withAnimation(KA.M.fade) {
                output = result
                running = false
            }
        }
    }
}

/// Act をアルファベットキーに割り当てる (長押しで実行)
@MainActor
struct AssignKeySheet: View {
    var act: ActItem
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    private let rows = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]

    var body: some View {
        let current = store.assignedKey(for: act)
        VStack(spacing: 18) {
            Capsule().fill(KA.C.textTertiary).frame(width: 36, height: 5).padding(.top, 8)
            Text("Assign to Key").font(KA.F.title).foregroundStyle(KA.C.text)
            Text("Long-press the key on the Keyact keyboard to run “\(act.name)”.")
                .font(KA.F.caption).foregroundStyle(KA.C.textSecondary).multilineTextAlignment(.center)
            VStack(spacing: 10) {
                ForEach(rows, id: \.self) { row in
                    HStack(spacing: 6) {
                        ForEach(Array(row), id: \.self) { ch in
                            let key = String(ch)
                            let owner = store.state.keyAssignments[key].flatMap { store.act($0) }
                            Button {
                                store.assign(act, toKey: key)
                                dismiss()
                            } label: {
                                Text(key.uppercased())
                                    .font(.system(size: 18, weight: .medium))
                                    .foregroundStyle(current == key ? KA.C.ctaText : KA.C.text)
                                    .frame(width: 30, height: 40)
                                    .background(RoundedRectangle(cornerRadius: 7).fill(current == key ? KA.C.ctaBg : KA.C.raised))
                                    .overlay(alignment: .topTrailing) {
                                        if owner != nil && current != key {
                                            Circle().fill(KA.C.accent).frame(width: 6, height: 6).padding(3)
                                        }
                                    }
                            }
                            .buttonStyle(.pressable)
                        }
                    }
                }
            }
            if current != nil {
                Button("Remove assignment", role: .destructive) {
                    store.assign(act, toKey: nil)
                    dismiss()
                }
                .font(KA.F.bodyMedium)
            }
            Spacer()
        }
        .padding(.horizontal, KA.S.screenHSettings)
        .screenBackground()
        .presentationDetents([.medium])
    }
}
