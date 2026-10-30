import SwiftUI
import AppKit
import UniformTypeIdentifiers

// ───────────────────────────────────────────── CLI 모델

struct ScanItem: Decodable, Identifiable {
    let path: String
    let rel: String
    let old: String
    let new: String
    let kind: String
    let action: String
    let note: String?
    var id: String { path }
    var willChange: Bool { action == "rename" }
}

struct VolumeInfo: Decodable {
    let mount: String?
    let fstype: String?
    let nfc_capable: Bool?
    let network: Bool?
    let note: String?
}

struct Counts: Decodable {
    let checked: Int
    let nfd: Int
    let rename: Int
    let skip: Int
    let clean: Int
}

struct ErrorLine: Decodable {
    let path: String?
    let error: String?
}

struct ScanResult: Decodable {
    let roots: [String]
    let volume: VolumeInfo?
    let counts: Counts
    let errors: [ErrorLine]?
    let items: [ScanItem]?
}

struct FailedItem: Decodable, Identifiable {
    let path: String
    let error: String
    var id: String { path }
}

struct FixResult: Decodable {
    let applied: Bool
    let batch: String
    let renamed: Int
    let failed: [FailedItem]
}

struct ZipVerify: Decodable {
    let non_nfc: Int
    let missing_utf8_flag: Int
}

struct ZipResult: Decodable {
    let out: String
    let entries: Int
    let names_normalized: Int
    let size: Int
    let verify: ZipVerify
}

struct UnzipResult: Decodable {
    let zip: String
    let dest: String
    let files: Int
    let names_repaired: Int
    let non_nfc_left: Int
}

struct WatchStatus: Decodable {
    let agent_installed: Bool
    let agent_running: Bool
    let paused: Bool
    let folders: [String]
    let quiet: Double?
    let stable: Double?
    let allow_projects: Bool?
}

struct UndoResult: Decodable {
    let restored: Int
    let failed: [FailedItem]?
}

extension ProcessInfo {
    /// "arm64" / "x86_64"
    var machineArchitecture: String {
        var info = utsname()
        uname(&info)
        return withUnsafeBytes(of: &info.machine) { raw -> String in
            let bytes = raw.prefix(while: { $0 != 0 })
            return String(decoding: bytes, as: UTF8.self)
        }
    }
}

// ───────────────────────────────────────────── 경로 / 일시정지 상태

enum HanFixPaths {
    static var home: String { FileManager.default.homeDirectoryForCurrentUser.path }
    static var stateDir: String { home + "/.hanfix" }
    static var pauseFile: String { stateDir + "/watch.paused" }
    static var logFile: String { stateDir + "/watch.log" }
    static var downloads: String { home + "/Downloads" }

    static var isPaused: Bool { FileManager.default.fileExists(atPath: pauseFile) }

    static func setPaused(_ paused: Bool) {
        try? FileManager.default.createDirectory(atPath: stateDir,
                                                 withIntermediateDirectories: true)
        if paused {
            FileManager.default.createFile(atPath: pauseFile, contents: nil)
        } else {
            try? FileManager.default.removeItem(atPath: pauseFile)
        }
    }
}

// ───────────────────────────────────────────── CLI 실행기

enum CLI {
    /// 앱에 내장한 파이썬 런타임 (없으면 시스템 파이썬으로 폴백)
    static var interpreter: String {
        if let res = Bundle.main.resourceURL {
            let arch = ProcessInfo.processInfo.machineArchitecture
            let bundled = res.appendingPathComponent("runtime/\(arch)/bin/python3").path
            if FileManager.default.isExecutableFile(atPath: bundled) { return bundled }
            let bundledAlt = res.appendingPathComponent("runtime/bin/python3").path
            if FileManager.default.isExecutableFile(atPath: bundledAlt) { return bundledAlt }
        }
        return "/usr/bin/python3"
    }

    /// 실행할 cli.py — 번들 우선, 개발 중이면 저장소 경로
    static var path: String {
        if let res = Bundle.main.resourceURL {
            let p = res.appendingPathComponent("hanfix/hanfix/cli.py").path
            if FileManager.default.fileExists(atPath: p) { return p }
            let alt = res.appendingPathComponent("hanfix/bin/hanfix").path
            if FileManager.default.isExecutableFile(atPath: alt) { return alt }
        }
        return FileManager.default.homeDirectoryForCurrentUser.path + "/hanfix/hanfix/cli.py"
    }

    struct Output {
        let stdout: String
        let stderr: String
        let code: Int32
    }

    static func run(_ args: [String], cwd: String? = nil) throws -> Output {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: interpreter)
        proc.arguments = [path] + args
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONPATH")
        env["PYTHONDONTWRITEBYTECODE"] = "1"   // 번들 안에 __pycache__ 금지(서명 보존)
        env["LANG"] = "en_US.UTF-8"
        proc.environment = env
        if let cwd { proc.currentDirectoryURL = URL(fileURLWithPath: cwd) }
        let outPipe = Pipe(), errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        try proc.run()
        let od = outPipe.fileHandleForReading.readDataToEndOfFile()
        let ed = errPipe.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        return Output(stdout: String(data: od, encoding: .utf8) ?? "",
                      stderr: String(data: ed, encoding: .utf8) ?? "",
                      code: proc.terminationStatus)
    }

    static func json<T: Decodable>(_ type: T.Type, _ args: [String], cwd: String? = nil) throws -> T {
        let o = try run(args, cwd: cwd)
        let data = o.stdout.data(using: .utf8) ?? Data()
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            let msg = o.stderr.isEmpty ? (o.stdout.isEmpty ? "알 수 없는 오류" : o.stdout) : o.stderr
            throw NSError(domain: "hanfix", code: Int(o.code),
                          userInfo: [NSLocalizedDescriptionKey: String(msg.prefix(400))])
        }
    }
}

// 앱 실행 직후 도착하는 '문서 열기' 이벤트를 잃지 않기 위한 큐
enum OpenQueue {
    private static let lock = NSLock()
    private static var paths: [String] = []
    static func add(_ p: [String]) {
        lock.lock(); paths.append(contentsOf: p); lock.unlock()
    }
    static func drain() -> [String] {
        lock.lock(); let r = paths; paths = []; lock.unlock(); return r
    }
}

// ───────────────────────────────────────────── 앱 상태

final class AppState: ObservableObject {
    static let shared = AppState()
    let home = FileManager.default.homeDirectoryForCurrentUser.path

    @Published var targets: [String] = []
    @Published var scan: ScanResult?
    @Published var busy = false
    @Published var status = "폴더를 끌어다 놓으세요"
    @Published var note = ""
    @Published var log: [String] = []
    @Published var zipTargets: [String] = []
    @Published var watch: WatchStatus?
    @Published var showSettings = false
    @Published var accessBlocked = false

    func loadWatchStatus() {
        bg {
            if let st = try? CLI.json(WatchStatus.self, ["watch", "--status", "--json"]) {
                DispatchQueue.main.async { self.watch = st }
            }
        }
    }

    /// macOS 폴더 접근 권한을 요청한다. 이 호출이 시스템 대화상자를 띄운다.
    /// (창 없는 백그라운드 프로세스가 먼저 접근하면 대화상자 없이 멈춰 버린다.)
    func requestFolderAccess() {
        let folders = (watch?.folders ?? []).map { ($0 as NSString).expandingTildeInPath }
        guard !folders.isEmpty else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            var blocked = 0
            for f in folders {
                if (try? FileManager.default.contentsOfDirectory(atPath: f)) == nil {
                    blocked += 1
                }
            }
            DispatchQueue.main.async {
                self.accessBlocked = blocked > 0
            }
        }
    }

    func openPrivacySettings() {
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!
        NSWorkspace.shared.open(url)
    }

    func toggleWatchPause() {
        bg {
            let paused = (try? CLI.json(WatchStatus.self, ["watch", "--status", "--json"]).paused) ?? false
            _ = try? CLI.run(["watch", paused ? "--resume" : "--pause"])
            if let st = try? CLI.json(WatchStatus.self, ["watch", "--status", "--json"]) {
                DispatchQueue.main.async { self.watch = st }
            }
        }
    }

    var isZipMode: Bool { !targets.isEmpty && targets.allSatisfy { $0.lowercased().hasSuffix(".zip") } }

    /// 앱 시작 시점에 도착해 대기 중인 열기 요청을 흡수한다.
    func consumePending() {
        let queued = OpenQueue.drain()
        if !queued.isEmpty { add(queued) }
    }

    func add(_ urls: [String]) {
        for u in urls where !targets.contains(u) {
            targets.append(u)
        }
        rescan()
    }

    func clear() {
        targets = []
        scan = nil
        zipTargets = []
        status = "폴더를 끌어다 놓으세요"
        note = ""
        log = []
    }

    private func bg(_ work: @escaping () -> Void) {
        busy = true
        DispatchQueue.global(qos: .userInitiated).async {
            work()
            DispatchQueue.main.async { self.busy = false }
        }
    }

    func rescan() {
        guard !targets.isEmpty else { clear(); return }
        bg {
            do {
                let r = try CLI.json(ScanResult.self, ["scan", "--json"] + self.targets)
                DispatchQueue.main.async {
                    self.scan = r
                    let c = r.counts
                    self.status = c.nfd == 0
                        ? "이상 없음 — \(c.checked)개 검사"
                        : "문제 \(c.nfd)개 발견 (정리 가능 \(c.rename))"
                    if let v = r.volume {
                        self.note = Self.volumeNote(v)
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    self.status = "스캔 실패"
                    self.note = error.localizedDescription
                }
            }
        }
    }

    static func volumeNote(_ v: VolumeInfo) -> String {
        let fs = v.fstype ?? "?"
        if v.nfc_capable == false {
            return "\(fs) 볼륨은 파일명을 NFD로 강제 저장합니다 — 이름 정리가 효과 없습니다."
        }
        if v.network == true {
            return "\(fs) 네트워크 볼륨 — 서버 저장 형태가 우선입니다. 변경 후 다시 확인하세요."
        }
        if v.nfc_capable == true && fs == "apfs" {
            return "apfs — NFC 이름이 그대로 저장됩니다."
        }
        if let n = v.note, !n.isEmpty { return n }
        return fs
    }

    func fix() {
        guard !targets.isEmpty else { return }
        bg {
            do {
                let r = try CLI.json(FixResult.self, ["fix", "--apply", "--json"] + self.targets)
                DispatchQueue.main.async {
                    self.log.insert("이름 정리 \(r.renamed)개 완료 (배치 \(r.batch))", at: 0)
                    for f in r.failed { self.log.insert("실패: \(f.path) — \(f.error)", at: 0) }
                    self.rescan()
                }
            } catch {
                DispatchQueue.main.async { self.log.insert("정리 실패: \(error.localizedDescription)", at: 0) }
            }
        }
    }

    func undo() {
        bg {
            do {
                let r = try CLI.json(UndoResult.self, ["undo", "--json"])
                DispatchQueue.main.async {
                    self.log.insert("되돌림 \(r.restored)개", at: 0)
                    if let f = r.failed, !f.isEmpty {
                        self.log.insert("되돌리기 실패 \(f.count)건", at: 0)
                    }
                    self.rescan()
                }
            } catch {
                DispatchQueue.main.async { self.log.insert("되돌리기 실패: \(error.localizedDescription)", at: 0) }
            }
        }
    }

    func makeZip() {
        guard !targets.isEmpty else { return }
        let cwd = (targets[0] as NSString).deletingLastPathComponent
        bg {
            do {
                let r = try CLI.json(ZipResult.self, ["zip", "--json"] + self.targets, cwd: cwd)
                DispatchQueue.main.async {
                    let ok = r.verify.non_nfc == 0 && r.verify.missing_utf8_flag == 0
                    self.log.insert("압축 \(r.entries)개 항목 · " + Self.human(r.size)
                                    + (ok ? " · 윈도우 안전 확인" : " · 검증 실패"), at: 0)
                    self.log.insert("→ \(r.out)", at: 0)
                }
            } catch {
                DispatchQueue.main.async { self.log.insert("압축 실패: \(error.localizedDescription)", at: 0) }
            }
        }
    }

    func unzip() {
        guard !targets.isEmpty else { return }
        bg {
            do {
                let r = try CLI.json([UnzipResult].self, ["unzip", "--json"] + self.targets)
                DispatchQueue.main.async {
                    for one in r {
                        self.log.insert("해제 \(one.files)개 · 이름 복원 \(one.names_repaired)건 → \(one.dest)", at: 0)
                    }
                }
            } catch {
                DispatchQueue.main.async { self.log.insert("해제 실패: \(error.localizedDescription)", at: 0) }
            }
        }
    }

    static func human(_ n: Int) -> String {
        var v = Double(n)
        for u in ["B", "KB", "MB", "GB"] {
            if v < 1024 { return u == "B" ? "\(Int(v))B" : String(format: "%.1f%@", v, u) }
            v /= 1024
        }
        return String(format: "%.1fTB", v)
    }
}

// ───────────────────────────────────────────── UI 조각

enum Palette {
    static let accent = Color(red: 0.11, green: 0.42, blue: 0.82)
    static let accentSoft = Color(red: 0.11, green: 0.42, blue: 0.82).opacity(0.07)
    static let warn = Color(red: 0.78, green: 0.42, blue: 0.04)
    static let good = Color(red: 0.13, green: 0.55, blue: 0.30)
    static let hairline = Color(red: 0.0, green: 0.0, blue: 0.0).opacity(0.12)
}

struct DropZone: View {
    @Binding var targeted: Bool
    let hasTargets: Bool
    let status: String
    var interactive: Bool = true        // 오프스크린 렌더에는 onDrop을 붙이지 않는다
    let onDrop: ([String]) -> Void

    @ViewBuilder
    var body: some View {
        if interactive {
            zone.onDrop(of: [UTType.fileURL], isTargeted: $targeted) { providers in
                let group = DispatchGroup()
                var urls: [String] = []
                let lock = NSLock()
                for p in providers {
                    group.enter()
                    _ = p.loadObject(ofClass: URL.self) { url, _ in
                        if let url {
                            lock.lock(); urls.append(url.path); lock.unlock()
                        }
                        group.leave()
                    }
                }
                group.notify(queue: .main) { onDrop(urls) }
                return true
            }
        } else {
            zone
        }
    }

    private var zone: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(targeted ? Palette.accent.opacity(0.13) : Palette.accentSoft)
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Palette.accent.opacity(targeted ? 1.0 : 0.35),
                              style: StrokeStyle(lineWidth: 1.5, dash: [7, 5]))
            VStack(spacing: 8) {
                Image(systemName: hasTargets ? "checkmark.circle" : "square.and.arrow.down")
                    .font(.system(size: 28, weight: .regular))
                    .foregroundStyle(hasTargets ? Palette.good : Palette.accent)
                Text(status)
                    .font(.system(size: 14, weight: .medium))
                Text("폴더·파일·zip — 여러 개 동시에")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            .padding(18)
        }
        .frame(height: 108)
    }
}

struct ItemRow: View {
    let item: ScanItem
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: item.kind == "dir" ? "folder.fill" : (item.kind == "link" ? "link" : "doc.fill"))
                .foregroundStyle(item.willChange ? Color.orange : Color.secondary)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.rel)
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                if item.willChange {
                    Text("→ " + item.new)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                } else if let n = item.note, !n.isEmpty {
                    Text(n).font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 8)
            Text(item.willChange ? "NFD" : "정상")
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .kerning(0.4)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(Capsule().fill(item.willChange ? Color.orange.opacity(0.14)
                                                            : Color.secondary.opacity(0.10)))
                .foregroundStyle(item.willChange ? Color.orange : Color.secondary)
        }
        .padding(.vertical, 5)
    }
}

// ───────────────────────────────────────────── 메인 화면

struct ContentView: View {
    @ObservedObject var state: AppState
    var flatList: Bool = false          // 오프스크린 스냅샷용(ScrollView 미사용)
    @State private var targeted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            DropZone(targeted: $targeted, hasTargets: !state.targets.isEmpty,
                     status: state.status, interactive: !flatList) { state.add($0) }
            if !state.note.isEmpty {
                Text(state.note)
                    .font(.system(size: 11.5))
                    .foregroundStyle(Color.primary.opacity(0.72))
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let scan = state.scan {
                summary(scan)
                list(scan)
            }
            logView
            watchBar
            Spacer(minLength: 0)
            buttons
        }
        .padding(18)
        .frame(minWidth: 640, minHeight: 560)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear {
            state.consumePending()
            if !flatList {
                state.loadWatchStatus()                // 스냅샷 모드는 busy 스피너 없이
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                    state.requestFolderAccess()        // 첫 실행 시 권한 대화상자 유도
                }
            }
        }
        .sheet(isPresented: $state.showSettings) {
            SettingsView(state: state)
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("HanFix").font(.system(size: 20, weight: .bold))
                Text("맥 한글 자소분리(NFD) 정리 — 파일명 · 압축파일")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Spacer()
            if state.busy {
                ProgressView().controlSize(.small)
            }
            Button {
                state.showSettings = true
            } label: {
                Label("설정", systemImage: "gearshape")
            }
            .buttonStyle(.plain)
            .font(.system(size: 11))
            .foregroundStyle(Palette.accent)
            .help("감시 폴더·자동 시작 설정 (⌘,)")

            Button("비우기") { state.clear() }
                .buttonStyle(.plain)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .help("대상 비우기")
        }
    }

    private func summary(_ scan: ScanResult) -> some View {
        HStack(spacing: 16) {
            metric("검사", "\(scan.counts.checked)", .secondary)
            metric("문제", "\(scan.counts.nfd)", scan.counts.nfd > 0 ? .orange : .green)
            metric("정리 가능", "\(scan.counts.rename)", .primary)
            metric("건너뜀", "\(scan.counts.skip)", .secondary)
            Spacer()
            if let v = scan.volume, let fs = v.fstype {
                Text(fs.uppercased())
                    .font(.system(size: 10, weight: .semibold))
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(Capsule().fill(Color.secondary.opacity(0.12)))
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func metric(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 10)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 16, weight: .semibold, design: .rounded))
                .foregroundStyle(color)
        }
    }

    private func listBody(_ items: [ScanItem]) -> some View {
        VStack(spacing: 0) {
            ForEach(items.prefix(500)) { it in
                ItemRow(item: it)
                Divider().opacity(0.4)
            }
        }
    }

    private func list(_ scan: ScanResult) -> some View {
        let items = scan.items ?? []
        return Group {
            if items.isEmpty {
                Text(scan.counts.nfd == 0 ? "정리할 항목이 없습니다." : "")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 8)
            } else {
                Group {
                    if flatList {
                        listBody(items).frame(height: 240, alignment: .top)
                    } else {
                        ScrollView { listBody(items) }
                            .frame(height: 240)
                            .scrollIndicators(.visible)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(Color.primary.opacity(0.08))
                )
            }
        }
    }

    private var logView: some View {
        Group {
            if !state.log.isEmpty {
                ScrollView {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(state.log.prefix(40).enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(maxHeight: 120)
                .scrollIndicators(.visible)
            }
        }
    }

    @ViewBuilder
    private var watchBar: some View {
        if let w = state.watch, w.agent_installed {
            HStack(spacing: 7) {
                Circle()
                    .fill(w.agent_running && !w.paused ? Palette.good : Color.secondary.opacity(0.45))
                    .frame(width: 7, height: 7)
                Text("자동 감시 " + (w.agent_running && !w.paused ? "켜짐" : (w.paused ? "일시정지" : "중지됨")))
                    .font(.system(size: 11, weight: .medium))
                Text(w.folders.map { ($0 as NSString).lastPathComponent }.joined(separator: " · "))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                if state.accessBlocked || (!w.agent_running && w.agent_installed) {
                    Text("권한 필요").font(.system(size: 10, weight: .semibold))
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Capsule().fill(Color.orange.opacity(0.18)))
                        .foregroundStyle(.orange)
                    Button("권한 설정 열기") { state.openPrivacySettings() }
                        .buttonStyle(.plain)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.orange)
                }
                Spacer()
                Button(w.paused ? "재개" : "일시정지") { state.toggleWatchPause() }
                    .buttonStyle(.plain)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Palette.accent)
            }
            .padding(.top, 2)
        }
    }

    private var buttons: some View {
        HStack(spacing: 8) {
            if state.isZipMode {
                Button { state.unzip() } label: {
                    Label("안전하게 풀기", systemImage: "archivebox")
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.busy || state.targets.isEmpty)
            } else {
                Button { state.fix() } label: {
                    Label("전체 이름 정리 (\(state.scan?.counts.rename ?? 0))",
                          systemImage: "wand.and.stars")
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.busy || (state.scan?.counts.rename ?? 0) == 0)
            }
            Button { state.makeZip() } label: {
                Label("윈도우용 압축", systemImage: "zipper")
            }
            .disabled(state.busy || state.targets.isEmpty || state.isZipMode)
            .help("NFC 이름 + UTF-8 플래그로 zip 생성")

            Button { state.unzip() } label: {
                Label("압축 풀기", systemImage: "archivebox")
            }
            .disabled(state.busy || state.targets.isEmpty)
            .help("CP949/UTF-8 이름 자동 판별 후 해제")

            Spacer()

            Button { state.undo() } label: {
                Label("되돌리기", systemImage: "arrow.uturn.backward")
            }
            .disabled(state.busy)
        }
    }
}

// ───────────────────────────────────────────── 앱 진입점

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static var pending: [String] = []
    private var child: Process?          // --watch-daemon 모드의 감시 자식 프로세스
    private var daemonMode = false
    func application(_ application: NSApplication, open urls: [URL]) {
        OpenQueue.add(urls.map(\.path))
        // AppState 는 뷰와 독립이므로, 뷰가 아직 만들어지지 않았어도 바로 반영된다.
        DispatchQueue.main.async { AppState.shared.consumePending() }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        !daemonMode          // 감시 모드에서는 창을 숨겨도 종료하지 않는다
    }

    // CLI·JSON 배관 통합 검증:  HanFix --selftest <경로>
    private func runSelftest(_ path: String) {
        func out(_ s: String) { print(s); fflush(stdout) }
        out("CLI=\(CLI.path)")
        out("python=\(CLI.interpreter)")
        out("번들 런타임=\(CLI.interpreter.contains("/Contents/Resources/runtime/"))")
        do {
            let scan = try CLI.json(ScanResult.self, ["scan", "--json", path])
            out("SCAN ok nfd=\(scan.counts.nfd) rename=\(scan.counts.rename) checked=\(scan.counts.checked) fs=\(scan.volume?.fstype ?? "?")")
            let fix = try CLI.json(FixResult.self, ["fix", "--apply", "--json", path])
            out("FIX ok renamed=\(fix.renamed) failed=\(fix.failed.count)")
            let rescan = try CLI.json(ScanResult.self, ["scan", "--json", path])
            out("RESCAN ok nfd=\(rescan.counts.nfd)")
            let undo = try CLI.json(UndoResult.self, ["undo", "--json"])
            out("UNDO ok restored=\(undo.restored) failed=\(undo.failed?.count ?? 0)")
            let cwd = (path as NSString).deletingLastPathComponent
            let zip = try CLI.json(ZipResult.self, ["zip", "--json", path], cwd: cwd)
            out("ZIP ok out=\(zip.out) entries=\(zip.entries) non_nfc=\(zip.verify.non_nfc) flag_missing=\(zip.verify.missing_utf8_flag)")
            let unz = try CLI.json([UnzipResult].self, ["unzip", "--json", zip.out])
            out("UNZIP ok files=\(unz.first?.files ?? -1) repaired=\(unz.first?.names_repaired ?? -1) left=\(unz.first?.non_nfc_left ?? -1)")
            out("SELFTEST PASS")
        } catch {
            out("SELFTEST FAIL: \(error.localizedDescription)")
        }
        exit(0)
    }

    // 화면 녹화 권한 없이 UI를 검증하기 위한 오프스크린 렌더: HanFix --snapshot <out.png> <경로>
    private func runSnapshot(_ out: String, path: String?) {
        let st = AppState()
        st.watch = try? CLI.json(WatchStatus.self, ["watch", "--status", "--json"])
        if let path {
            st.targets = [path]
            if let r = try? CLI.json(ScanResult.self, ["scan", "--json", path]) {
                st.scan = r
                st.status = "문제 \(r.counts.nfd)개 발견 (정리 가능 \(r.counts.rename))"
                if let v = r.volume { st.note = AppState.volumeNote(v) }
            }
        }
        st.log = ["압축 128개 항목 · 42.7MB · 윈도우 안전 확인",
                  "→ /Users/me/제안서_최종.zip",
                  "이름 정리 34개 완료 (배치 20261026-2231-7f3a9c)"]
        let view = ContentView(state: st, flatList: true).frame(width: 900, height: 620)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        if let img = renderer.nsImage, let tiff = img.tiffRepresentation,
           let rep = NSBitmapImageRep(data: tiff),
           let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: out))
            print("SNAPSHOT \(out)")
        } else {
            print("SNAPSHOT FAILED")
        }
        exit(0)
    }

    // ── 상시 감시 호스트 ────────────────────────────────────────────
    // launchd 가 직접 python 을 띄우면 TCC(개인정보 보호)에 막혀 다운로드/문서 폴더를
    // 읽을 수 없다. 앱이 부모가 되어 자식을 띄우면, 앱에 부여된 권한을 자식이 물려받는다.
    private var statusItem: NSStatusItem?

    private func runWatchDaemon() {
        daemonMode = true
        NSApp.setActivationPolicy(.accessory)
        DispatchQueue.main.async {
            for w in NSApp.windows { w.orderOut(nil) }
            self.installStatusItem()
        }
        startWatcher()
    }

    // 메뉴 막대 상주 아이콘 — 감시가 살아 있는지 눈으로 확인할 수 있게.
    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            button.image = NSImage(systemSymbolName: "textformat.abc",
                                   accessibilityDescription: "HanFix 감시 중")
            button.image?.isTemplate = true
            button.toolTip = "HanFix — 자동 감시 중"
        }
        let menu = NSMenu()
        menu.addItem(withTitle: "HanFix 창 열기", action: #selector(showMainWindow), keyEquivalent: "")
        menu.addItem(.separator())
        let pause = NSMenuItem(title: "일시정지", action: #selector(togglePause), keyEquivalent: "")
        pause.state = HanFixPaths.isPaused ? .on : .off
        pause.tag = 100
        menu.addItem(pause)
        menu.addItem(withTitle: "감시 폴더 열기", action: #selector(openWatchFolder), keyEquivalent: "")
        menu.addItem(withTitle: "로그 보기", action: #selector(openLog), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "자동 감시 끄기", action: #selector(stopWatching), keyEquivalent: "")
        for mi in menu.items where mi.action != nil { mi.target = self }
        item.menu = menu
        statusItem = item
    }

    @objc private func showMainWindow() {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        if let w = NSApp.windows.first(where: { $0.contentViewController != nil || $0.canBecomeMain }) {
            w.makeKeyAndOrderFront(nil)
        } else {
            NSApp.windows.forEach { $0.makeKeyAndOrderFront(nil) }
        }
    }

    @objc private func togglePause(_ sender: NSMenuItem) {
        let now = !HanFixPaths.isPaused
        HanFixPaths.setPaused(now)
        sender.state = now ? .on : .off
        sender.title = now ? "일시정지" : "일시정지"
    }

    @objc private func openWatchFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: HanFixPaths.downloads))
    }

    @objc private func openLog() {
        NSWorkspace.shared.open(URL(fileURLWithPath: HanFixPaths.logFile))
    }

    @objc private func stopWatching() {
        // launchd 작업을 먼저 해제해야 KeepAlive 가 다시 띄우지 않는다.
        let label = "com.crispartners.hanfix.watch"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["bootout", "gui/\(getuid())/\(label)"]
        try? p.run()
        p.waitUntilExit()
        child?.terminate()
        child = nil
        NSApp.terminate(nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if daemonMode && !flag { showMainWindow() }
        return true
    }

    private func startWatcher() {
        let cli = CLI.path
        guard FileManager.default.fileExists(atPath: cli) else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: CLI.interpreter)   // 내장 런타임 우선
        p.arguments = [cli, "watch", "--daemon"]
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONPATH")
        env["PYTHONDONTWRITEBYTECODE"] = "1"   // 번들 안에 __pycache__ 금지(서명 보존)
        p.environment = env
        let logPath = FileManager.default.homeDirectoryForCurrentUser.path + "/.hanfix/child.log"
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        if let fh = FileHandle(forWritingAtPath: logPath) {
            fh.seekToEndOfFile()
            let diag = "[\(Date())] interpreter=\(CLI.interpreter) arch=\(ProcessInfo.processInfo.machineArchitecture) cli=\(cli)\n"
            fh.write(diag.data(using: .utf8) ?? Data())
            p.standardOutput = fh
            p.standardError = fh
        }
        p.terminationHandler = { proc in
            // 종료 코드 3 = 폴더 접근 권한 없음 → 시도 간격을 늘려 재시도 폭주를 막는다
            let delay: Double = proc.terminationStatus == 3 ? 60 : 5
            DispatchQueue.main.async {
                AppState.shared.accessBlocked = (proc.terminationStatus == 3)
                self.statusItem?.button?.toolTip = proc.terminationStatus == 3
                    ? "HanFix — 폴더 접근 권한이 필요합니다" : "HanFix — 자동 감시 중"
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                self?.startWatcher()
            }
        }
        do {
            try p.run()
            child = p
        } catch {
            print("감시자 시작 실패: \(error)")
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = ProcessInfo.processInfo.arguments
        if args.contains("--watch-daemon") {
            runWatchDaemon()
            return
        }
        if let i = args.firstIndex(of: "--selftest"), i + 1 < args.count {
            runSelftest(args[i + 1])
        }
        if let i = args.firstIndex(of: "--snapshot-settings"), i + 1 < args.count {
            let st = AppState.shared
            st.watch = WatchStatus(agent_installed: true, agent_running: true, paused: false,
                                   folders: [st.home + "/Downloads", st.home + "/Desktop",
                                             st.home + "/Documents"],
                                   quiet: 2.0, stable: 2.0, allow_projects: false)
            let view = SettingsView(state: st, flat: true)
            let renderer = ImageRenderer(content: view)
            renderer.scale = 2
            if let img = renderer.nsImage, let tiff = img.tiffRepresentation,
               let rep = NSBitmapImageRep(data: tiff),
               let png = rep.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: args[i + 1]))
                print("SNAPSHOT SETTINGS \(args[i + 1])")
            }
            exit(0)
        }
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            runSnapshot(args[i + 1], path: i + 2 < args.count ? args[i + 2] : nil)
        }
        // 문서 열기(Finder 드래그/연결 프로그램) 경로 검증: --opentest <경로>
        if let i = args.firstIndex(of: "--opentest"), i + 1 < args.count {
            let path = args[i + 1]
            DispatchQueue.main.async {
                self.application(NSApp, open: [URL(fileURLWithPath: path)])
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
                let st = AppState.shared
                print("OPENTEST targets=\(st.targets.count) status=\(st.status) nfd=\(st.scan?.counts.nfd ?? -1) rename=\(st.scan?.counts.rename ?? -1)")
                print(st.targets.isEmpty ? "OPENTEST FAIL" : "OPENTEST PASS")
                exit(0)
            }
        }
    }
}

@main
struct HanFixApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    private var state = AppState.shared

    var body: some Scene {
        Window("HanFix", id: "main") {
            ContentView(state: state)
        }
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(replacing: .appSettings) {
                Button("설정…") { state.showSettings = true }
                    .keyboardShortcut(",", modifiers: .command)
            }
        }
    }
}
