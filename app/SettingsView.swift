import SwiftUI
import AppKit

// ───────────────────────────────────────────── 설정 창

struct SettingsView: View {
    @ObservedObject var state: AppState
    var flat: Bool = false          // 오프스크린 렌더용 (ScrollView 미사용)
    @Environment(\.dismiss) private var dismiss

    @State private var folders: [String] = []
    @State private var autoStart = false
    @State private var protectProjects = true
    @State private var quiet: Double = 2.0
    @State private var stable: Double = 2.0
    @State private var loaded = false
    @State private var busy = false
    @State private var message = ""
    @State private var messageIsError = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            Group {
                let body = VStack(alignment: .leading, spacing: 22) {
                    folderSection
                    behaviorSection
                    runtimeSection
                }
                .padding(22)
                if flat {
                    body
                } else {
                    ScrollView { body }
                }
            }
            .frame(maxHeight: 420)
            Divider()
            footer
        }
        .frame(width: 620, height: 560)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear(perform: load)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("설정").font(.system(size: 17, weight: .semibold))
            Text("어느 폴더를, 어떻게 자동 정리할지 정합니다.")
                .font(.system(size: 11)).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 22).padding(.top, 18).padding(.bottom, 14)
    }

    // ── 감시 폴더
    private var folderSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            label("감시할 폴더", "새로 만들어지거나 옮겨온 파일의 이름을 자동으로 정리합니다.")
            VStack(spacing: 0) {
                ForEach(Array(folders.enumerated()), id: \.element) { idx, path in
                    HStack(spacing: 8) {
                        Image(systemName: "folder.fill")
                            .font(.system(size: 11))
                            .foregroundStyle(Palette.accent)
                        Text(prettyPath(path))
                            .font(.system(size: 12, design: .monospaced))
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer()
                        Button {
                            folders.remove(at: idx)
                        } label: {
                            Image(systemName: "minus.circle")
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(.secondary)
                        .help("목록에서 제거")
                    }
                    .padding(.vertical, 6).padding(.horizontal, 10)
                    Divider().opacity(0.35)
                }
                HStack {
                    Button {
                        pickFolder()
                    } label: {
                        Label("폴더 추가…", systemImage: "plus")
                    }
                    .buttonStyle(.plain)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.accent)
                    Spacer()
                    if folders.isEmpty {
                        Text("폴더를 하나 이상 추가하세요")
                            .font(.system(size: 11)).foregroundStyle(.orange)
                    }
                }
                .padding(.vertical, 8).padding(.horizontal, 10)
            }
            .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.035)))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Palette.hairline))
        }
    }

    // ── 동작
    private var behaviorSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            label("동작", nil)
            Toggle("로그인할 때 자동 시작", isOn: $autoStart)
                .font(.system(size: 12))
            Toggle("코드/프로젝트 폴더 보호 (git, Xcode 등 내부는 건드리지 않음)",
                   isOn: $protectProjects)
                .font(.system(size: 12))
            HStack(spacing: 12) {
                Text("파일이 안정될 때까지 대기").font(.system(size: 12))
                Slider(value: $stable, in: 0.5...10, step: 0.5).frame(width: 150)
                Text(String(format: "%.1f초", stable))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 12) {
                Text("이벤트 묶음 대기").font(.system(size: 12))
                Slider(value: $quiet, in: 0.2...5, step: 0.1).frame(width: 150)
                Text(String(format: "%.1f초", quiet))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            Text("대기 시간이 짧을수록 빠르지만, 아직 쓰고 있는 파일을 건드릴 위험이 커집니다. 기본값을 권장합니다.")
                .font(.system(size: 10.5)).foregroundStyle(.secondary)
        }
    }

    // ── 현재 상태
    private var runtimeSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            label("현재 상태", nil)
            HStack(spacing: 10) {
                Circle()
                    .fill(state.watch?.agent_running == true ? Palette.good : Color.secondary.opacity(0.4))
                    .frame(width: 8, height: 8)
                Text(state.watch?.agent_running == true ? "감시 실행 중" : "감시 중지됨")
                    .font(.system(size: 12, weight: .medium))
                if state.watch?.paused == true {
                    Text("(일시정지)").font(.system(size: 11)).foregroundStyle(.orange)
                }
                Spacer()
                Button(state.watch?.paused == true ? "재개" : "일시정지") {
                    state.toggleWatchPause()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { state.loadWatchStatus() }
                }
                .buttonStyle(.plain)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Palette.accent)
            }
            Text("로그: ~/.hanfix/watch.log   ·   설정 파일: ~/.hanfix/watch.json")
                .font(.system(size: 10.5, design: .monospaced))
                .foregroundStyle(.secondary)
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if !message.isEmpty {
                Text(message)
                    .font(.system(size: 11))
                    .foregroundStyle(messageIsError ? Color.red : Palette.good)
                    .lineLimit(2)
            }
            Spacer()
            if busy { ProgressView().controlSize(.small) }
            Button("취소") { dismiss() }
                .keyboardShortcut(.cancelAction)
            Button("저장") { save() }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                .disabled(busy || folders.isEmpty)
        }
        .padding(.horizontal, 22).padding(.vertical, 14)
    }

    // ── 헬퍼
    private func label(_ title: String, _ subtitle: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 12, weight: .semibold))
            if let subtitle {
                Text(subtitle).font(.system(size: 10.5)).foregroundStyle(.secondary)
            }
        }
    }

    private func prettyPath(_ p: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return p.hasPrefix(home) ? "~" + p.dropFirst(home.count) : p
    }

    private func pickFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = true
        panel.prompt = "추가"
        panel.message = "자동 정리할 폴더를 선택하세요"
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser
        if panel.runModal() == .OK {
            for url in panel.urls where !folders.contains(url.path) {
                folders.append(url.path)
            }
        }
    }

    private func load() {
        guard !loaded else { return }
        loaded = true
        folders = state.watch?.folders.map {
            ($0 as NSString).expandingTildeInPath
        } ?? ["\(FileManager.default.homeDirectoryForCurrentUser.path)/Downloads"]
        autoStart = state.watch?.agent_installed ?? false
        protectProjects = (state.watch?.allow_projects ?? false) == false
        quiet = state.watch?.quiet ?? 2.0
        stable = state.watch?.stable ?? 2.0
    }

    private func save() {
        busy = true
        message = ""
        let target = folders

        DispatchQueue.global(qos: .userInitiated).async {
            var ok = true
            var note = "저장했습니다."

            // 1) 설정 파일 (경로는 ~ 표기로 저장 → 사용자 이동에 영향 없음)
            let home = FileManager.default.homeDirectoryForCurrentUser.path
            let stored = target.map { p -> String in
                p.hasPrefix(home) ? "~" + p.dropFirst(home.count) : p
            }
            let cfg: [String: Any] = [
                "folders": stored,
                "quiet": quiet,
                "stable": stable,
                "allow_projects": !protectProjects,
                "skip_projects": protectProjects,
            ]
            let dir = home + "/.hanfix"
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            if let data = try? JSONSerialization.data(withJSONObject: cfg,
                                                      options: [.prettyPrinted, .sortedKeys]) {
                try? data.write(to: URL(fileURLWithPath: dir + "/watch.json"))
            } else {
                ok = false; note = "설정 파일을 쓰지 못했습니다."
            }

            // 2) 자동 시작 등록/해제
            let appPath = Bundle.main.bundlePath
            if appPath.hasPrefix("/Volumes/") {
                ok = false
                note = "DMG에서 실행 중입니다. 먼저 응용 프로그램 폴더로 옮긴 뒤 설정하세요."
            } else if autoStart {
                if let out = try? CLI.run(["watch", "--install"]), out.code != 0 {
                    ok = false
                    note = "자동 시작 등록 실패: " + out.stderr.prefix(120)
                }
            } else {
                _ = try? CLI.run(["watch", "--uninstall"])
            }

            DispatchQueue.main.async {
                busy = false
                messageIsError = !ok
                message = note
                state.loadWatchStatus()
                if ok { DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { dismiss() } }
            }
        }
    }
}
