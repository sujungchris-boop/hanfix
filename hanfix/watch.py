"""
hanfix.watch — 지정한 폴더를 상시 감시하며 새로 생기는 NFD 이름을 자동으로 NFC로 정리.

설계
  * FSEvents(CoreServices)로 파일 단위 이벤트를 받는다. 폴링하지 않는다.
  * 이벤트를 quiet 초 동안 모았다가 한 번에 처리(디바운스).
  * 아직 쓰고 있는 파일(다운로드 중)은 건드리지 않는다: 임시 확장자 제외 + mtime 안정화 확인.
  * 프로젝트 폴더(.git, package.json …) 안쪽은 자동 변경하지 않는다 — git 작업트리가 깨진다.
  * 이름만 바꾼다. 내용은 절대 건드리지 않는다. 모든 변경은 저널에 남아 undo 가능.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import stat as statmod
import sys
import threading
import time
import traceback

from . import core

# ───────────────────────────────────────────── FSEvents 바인딩

_CS = "/System/Library/Frameworks/CoreServices.framework/CoreServices"
_CF = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

UTF8 = 0x08000100
SINCE_NOW = 0xFFFFFFFFFFFFFFFF
FLAG_FILE_EVENTS = 0x00000010
FLAG_NO_DEFER = 0x00000002
FLAG_WATCH_ROOT = 0x00000004

FSEVENT_ITEM_REMOVED = 0x00000400
FSEVENT_ITEM_RENAMED = 0x00000200


class FSWatcher:
    """여러 폴더를 하나의 FSEventStream 으로 감시."""

    def __init__(self, folders, on_batch, latency=0.4):
        self.folders = [os.path.abspath(os.path.expanduser(f)) for f in folders]
        self.on_batch = on_batch
        self.latency = latency
        self._callback = None          # GC 되면 세그폴트 → 반드시 인스턴스에 보관
        self._stream = None
        self._queue = None
        self._libcf = ctypes.CDLL(_CF)
        self._libcs = ctypes.CDLL(_CS)
        self._libsys = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        self._setup()

    def _setup(self):
        cf, cs, libsys = self._libcf, self._libcs, self._libsys

        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFArrayCreate.restype = ctypes.c_void_p
        cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.c_long, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        cs.FSEventStreamCreate.restype = ctypes.c_void_p
        cs.FSEventStreamStart.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamStop.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamInvalidate.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamRelease.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamSetDispatchQueue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        libsys.dispatch_queue_create.restype = ctypes.c_void_p
        libsys.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]

        callback_type = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        # argtypes 를 지정하지 않으면 float(latency) 변환에서 ArgumentError 가 난다.
        cs.FSEventStreamCreate.argtypes = [
            ctypes.c_void_p, callback_type, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_uint64, ctypes.c_double, ctypes.c_uint32]

        def _cb(stream, info, num, paths, flags, ids):
            try:
                arr = ctypes.cast(paths, ctypes.POINTER(ctypes.c_char_p))
                fl = ctypes.cast(flags, ctypes.POINTER(ctypes.c_uint32))
                batch = []
                for i in range(num):
                    raw = arr[i]
                    if not raw:
                        continue
                    batch.append((raw.decode("utf-8", "replace"), int(fl[i])))
                if batch:
                    self.on_batch(batch)
            except Exception:
                self._log_exc()

        self._callback = callback_type(_cb)

        arr = (ctypes.c_void_p * len(self.folders))()
        for i, f in enumerate(self.folders):
            arr[i] = cf.CFStringCreateWithCString(None, f.encode("utf-8"), UTF8)
        path_array = cf.CFArrayCreate(None, arr, len(self.folders),
                                     ctypes.byref(ctypes.c_void_p.in_dll(cf, "kCFTypeArrayCallBacks")))

        self._stream = cs.FSEventStreamCreate(
            None, self._callback, None, path_array, SINCE_NOW, self.latency,
            FLAG_FILE_EVENTS | FLAG_NO_DEFER | FLAG_WATCH_ROOT)
        if not self._stream:
            raise RuntimeError("FSEventStreamCreate 실패")
        self._queue = libsys.dispatch_queue_create(b"com.crispartners.hanfix.watch", None)
        cs.FSEventStreamSetDispatchQueue(self._stream, self._queue)
        if not cs.FSEventStreamStart(self._stream):
            raise RuntimeError("FSEventStreamStart 실패")

    def _log_exc(self):
        traceback.print_exc()

    def stop(self):
        cs = self._libcs
        if self._stream:
            cs.FSEventStreamStop(self._stream)
            cs.FSEventStreamInvalidate(self._stream)
            cs.FSEventStreamRelease(self._stream)
            self._stream = None


# ───────────────────────────────────────────── 자동 정리 정책

# 다운로드 중인 파일 — 이름을 바꾸면 브라우저가 마무리하지 못한다.
PARTIAL_EXTS = {
    ".crdownload", ".download", ".opdownload", ".part", ".partial",
    ".tmp", ".temp", ".filepart", ".!ut", ".aria2", ".td", ".bc!",
}

# 프로젝트/코드 폴더: 이름을 바꾸면 git 작업트리·빌드가 깨진다.
PROJECT_MARKERS = {
    ".git", ".hg", ".svn", "package.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "Podfile", "Gemfile", "composer.json", "Makefile", "CMakeLists.txt",
    "tsconfig.json", "build.gradle", "pom.xml", "requirements.txt",
}
PROJECT_SUFFIXES = (".xcodeproj", ".xcworkspace", ".sln", ".csproj", ".gradle")

# 절대 손대지 않는 디렉터리
SKIP_DIR_NAMES = {".git", ".hg", ".svn", "node_modules", ".venv", "venv",
                  "__pycache__", ".Trash", "Library"}


class AutoFixer:
    """이벤트를 모아 안전한 것만 실제로 rename 한다."""

    def __init__(self, roots, quiet=2.0, stable=2.0, log=None,
                 allow_projects=False, dry_run=False):
        self.roots = [os.path.abspath(os.path.expanduser(r)) for r in roots]
        self.quiet = quiet
        self.stable = stable
        self.log = log or (lambda msg: None)
        self.allow_projects = allow_projects
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self._pending: dict[str, float] = {}
        self._timer: threading.Timer | None = None
        self._cache: dict[str, tuple[float, bool]] = {}
        self._done: dict[str, float] = {}       # 정리 직후 재유입 차단
        self._denied: set[str] = set()
        self.stats = {"renamed": 0, "skipped": 0, "ignored": 0}

    def _already_done(self, path) -> bool:
        t = self._done.get(path)
        if t is None:
            return False
        if time.time() - t > 10:
            self._done.pop(path, None)
            return False
        return True

    # ── 이벤트 수신
    def feed(self, batch):
        now = time.time()
        with self._lock:
            for path, flags in batch:
                if flags & FSEVENT_ITEM_REMOVED:
                    continue
                self._pending[path] = now
            self._arm(now)

    def _arm(self, now):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.quiet, self._flush)
        self._timer.daemon = True
        self._timer.start()

    # ── 판단
    def _in_project(self, path) -> bool:
        d = os.path.dirname(path)
        seen = []
        while True:
            hit = self._cache.get(d)
            if hit and time.time() - hit[0] < 60:
                result = hit[1]
                break
            result = False
            try:
                names = set(os.listdir(d))
            except OSError:
                names = set()
            if names & PROJECT_MARKERS or any(n.endswith(PROJECT_SUFFIXES) for n in names):
                result = True
            seen.append(d)
            if result or d in self.roots or d == "/" or d == os.path.dirname(d):
                break
            d = os.path.dirname(d)
        for s in seen:
            self._cache[s] = (time.time(), result)
        return result

    def _anchor_root(self, path):
        for r in self.roots:
            if path == r or path.startswith(r.rstrip("/") + "/"):
                return r
        return None

    def _should_skip(self, path) -> str | None:
        name = os.path.basename(path.rstrip("/"))
        if not name or name in (".DS_Store",):
            return "ignore"
        if core.is_nfc(name):
            return "ok"                     # 이미 정상
        ext = os.path.splitext(name)[1].lower()
        if ext in PARTIAL_EXTS:
            return "partial"
        if ext in core.PACKAGE_EXTS:
            return "package"
        if self._anchor_root(path) is None:
            return "outside"
        parts = set(path.split(os.sep))
        if parts & SKIP_DIR_NAMES:
            return "ignoredir"
        if not self.allow_projects and self._in_project(path):
            return "project"
        return None

    def _is_stable(self, path) -> bool:
        """최근 stable 초 동안 크기·mtime 이 변하지 않았으면 다 쓴 파일로 본다."""
        try:
            st = os.lstat(path)
        except OSError:
            return False
        if statmod.S_ISDIR(st.st_mode) or statmod.S_ISLNK(st.st_mode):
            return True
        if time.time() - st.st_mtime < self.stable:
            return False
        return True

    # ── 시작 시 보정
    def catch_up(self, max_age=300):
        """감시자가 켜지는 순간의 사각지대(시작 직전 생성된 파일)를 메운다.

        FSEvents 는 스트림 생성 이후 이벤트만 주므로, 재시작 직후 몇 초 사이에
        만들어진 파일은 영영 놓친다. 최근 max_age 초 내 변경분만 훑어 보정한다.
        """
        now = time.time()
        found = []
        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in SKIP_DIR_NAMES
                               and os.path.splitext(d)[1].lower() not in core.PACKAGE_EXTS]
                for n in dirnames + filenames:
                    if core.is_nfc(n):
                        continue
                    p = os.path.join(dirpath, n)
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if now - st.st_mtime <= max_age:
                        found.append((p, 0))
        if found:
            self.log(f"시작 보정 — 최근 {max_age}초 내 NFD {len(found)}건")
            self.feed(found)

    # ── 실행
    def _flush(self):
        if is_paused():
            with self._lock:
                self._pending.clear()
                self._timer = None
            return
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
            self._timer = None

        # APFS 는 정규화를 무시하므로 lexists(nfd_path) 는 이미 고친 파일에도 True 다.
        # 디렉터리 실제 목록에서 '정확히 같은 문자열'이 있는지로 판단해야 한다.
        listings: dict[str, set] = {}

        def exists_exact(p) -> bool:
            d, n = os.path.dirname(p), os.path.basename(p)
            if d not in listings:
                try:
                    listings[d] = set(os.listdir(d))
                except OSError as ex:
                    listings[d] = set()
                    self._denied.add(d)
                    self.log(f"접근 거부 {d} ({ex.strerror}) — 권한 설정이 필요합니다")
            return n in listings[d]

        ready, wait_more = [], []
        for p in paths:
            if self._already_done(p):
                continue
            reason = self._should_skip(p)
            if reason in ("ok", "partial"):
                continue
            if os.environ.get("HANFIX_WATCH_DEBUG"):
                self.log(f"이벤트 {os.path.basename(p)} → {reason or '대상'}")
            if reason in ("ignore", "package", "outside", "ignoredir", "project"):
                # 실제 존재하고 NFD인데 정책상 건너뛴 경우만 기록
                if reason in ("package", "project") and exists_exact(p):
                    self.log(f"건너뜀({reason}) {os.path.basename(p)}")
                continue
            if not exists_exact(p):
                continue                     # 방금 정리했거나 사라짐(별칭 경로)
            if not self._is_stable(p):
                wait_more.append(p)          # 아직 쓰는 중 → 다음 라운드
                continue
            ready.append(p)

        if wait_more:
            with self._lock:
                for p in wait_more:
                    self._pending[p] = time.time()
                self._arm(time.time())

        if not ready:
            return

        try:
            entries = core.plan_paths(ready)
        except Exception:
            self.log("계획 수립 실패\n" + traceback.format_exc())
            return

        todo = [e for e in entries if e.action == "rename"]
        for e in entries:
            if e.action == "skip":
                self.stats["skipped"] += 1
                self.log(f"건너뜀(충돌) {e.old}")
        if not todo:
            return

        if self.dry_run:
            for e in todo:
                self.log(f"[예행] {e.old} → {e.new}")
            return

        out = core.apply_fix(todo, dry_run=False)
        self.stats["renamed"] += out["renamed"]
        now = time.time()
        for it in out["items"]:
            self._done[it["old"]] = now
            self._done[it["new"]] = now
        for it in out["items"]:
            self.log(f"정리 {os.path.basename(it['old'])} → {os.path.basename(it['new'])}")
        for f in out["failed"]:
            self.log(f"실패 {os.path.basename(f['path'])} — {f['error']}")


# ───────────────────────────────────────────── 데몬 구동

DEFAULT_FOLDERS = ["~/Downloads", "~/Desktop", "~/Documents"]
CONFIG_PATH = os.path.join(core.HOME, ".hanfix", "watch.json")
PAUSE_FILE = os.path.join(core.HOME, ".hanfix", "watch.paused")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    else:
        cfg = {}
    cfg.setdefault("folders", DEFAULT_FOLDERS)
    cfg.setdefault("quiet", 2.0)
    cfg.setdefault("stable", 2.0)
    cfg.setdefault("allow_projects", False)
    cfg.setdefault("skip_projects", True)
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def log_path() -> str:
    return os.environ.get("HANFIX_WATCH_LOG") or os.path.join(core.HOME, ".hanfix", "watch.log")


class Logger:
    def __init__(self, path=None, echo=True, max_bytes=512 * 1024):
        self.path = path or log_path()
        self.echo = echo
        self.max_bytes = max_bytes
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def __call__(self, msg: str):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
        if self.echo:
            print(line, flush=True)
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def probe_access(path, timeout=2.0):
    """폴더 접근이 가능한지 확인한다. TCC 가 대기 상태에 빠지면 hang 하므로 스레드로 감싼다.

    True  : 읽을 수 있음
    False : 거부됨(권한 필요)
    None  : 판정 불가(응답 없음 = 권한 대화상자 대기 중)
    """
    import queue as _q
    result: _q.Queue = _q.Queue(maxsize=1)

    def _try():
        try:
            os.listdir(path)
            result.put(True)
        except PermissionError:
            result.put(False)
        except OSError:
            result.put(False)

    threading.Thread(target=_try, daemon=True).start()
    try:
        return result.get(timeout=timeout)
    except _q.Empty:
        return None


def is_paused() -> bool:
    return os.path.exists(PAUSE_FILE)


AGENT_LABEL = "com.crispartners.hanfix.watch"
AGENT_PLIST = os.path.join(core.HOME, "Library", "LaunchAgents", AGENT_LABEL + ".plist")

LOCAL_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py")
INSTALLED_APP = "/Applications/HanFix.app"


def app_bundle_root() -> str | None:
    """이 코드가 HanFix.app 안에 있으면 그 .app 경로를 돌려준다(설치 위치 무관)."""
    p = os.path.abspath(__file__)
    for _ in range(6):
        p = os.path.dirname(p)
        if p.endswith(".app"):
            return p
        if p == "/" or not p:
            break
    return None


def app_binary() -> str | None:
    """감시자를 띄울 앱 실행 파일. 번들 안이면 자기 번들, 아니면 설치본."""
    for root in (app_bundle_root(), INSTALLED_APP if os.path.isdir(INSTALLED_APP) else None):
        if not root:
            continue
        bin_path = os.path.join(root, "Contents", "MacOS", "HanFix")
        if os.path.exists(bin_path):
            return bin_path
    return None


def cli_script_path() -> str:
    """LaunchAgent가 실행할 cli.py — 번들 안이면 번들, 아니면 설치본, 아니면 소스."""
    for root in (app_bundle_root(), INSTALLED_APP if os.path.isdir(INSTALLED_APP) else None):
        if not root:
            continue
        p = os.path.join(root, "Contents", "Resources", "hanfix", "hanfix", "cli.py")
        if os.path.exists(p):
            return p
    return LOCAL_CLI


def _launchctl(*args) -> tuple[int, str]:
    import subprocess
    p = subprocess.run(["/bin/launchctl"] + list(args), capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def agent_installed() -> bool:
    return os.path.exists(AGENT_PLIST)


def agent_pid() -> int | None:
    rc, out = _launchctl("print", f"gui/{os.getuid()}/{AGENT_LABEL}")
    if rc != 0:
        return None
    for line in out.splitlines():
        if "pid = " in line:
            try:
                return int(line.split("pid = ")[1].split()[0])
            except (IndexError, ValueError):
                return None
    return None


def install_agent(echo=True) -> int:
    os.makedirs(os.path.dirname(AGENT_PLIST), exist_ok=True)
    os.makedirs(os.path.join(core.HOME, ".hanfix"), exist_ok=True)
    logfile = os.path.join(core.HOME, ".hanfix", "launchd.log")
    script = cli_script_path()
    # 앱 바이너리를 실행해야 TCC 권한을 물려받는다(다운로드/문서 폴더 접근).
    app_bin = app_binary()
    if app_bin:
        program = [app_bin, "--watch-daemon"]
    else:
        program = ["/usr/bin/python3", script, "watch", "--daemon"]
    prog_xml = "".join(f"        <string>{a}</string>\n" for a in program)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{prog_xml}    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>{logfile}</string>
    <key>StandardErrorPath</key><string>{logfile}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key><string></string>
        <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
    </dict>
</dict>
</plist>
"""
    with open(AGENT_PLIST, "w", encoding="utf-8") as f:
        f.write(plist)

    _launchctl("bootout", f"gui/{os.getuid()}/{AGENT_LABEL}")
    import subprocess as _sp
    _sp.run(["/usr/bin/pkill", "-f", "cli.py watch --daemon"], capture_output=True)
    time.sleep(0.3)
    rc, out = _launchctl("bootstrap", f"gui/{os.getuid()}", AGENT_PLIST)
    if rc != 0:                                  # 구형 경로 폴백
        rc, out = _launchctl("load", "-w", AGENT_PLIST)
    if rc != 0:
        if echo:
            print(f"등록 실패: {out}")
        return 1
    if echo:
        print(f"등록 완료 — {AGENT_LABEL}")
        print(f"  실행 스크립트 {script}")
        print(f"  로그 {log_path()}")
    return 0


def uninstall_agent(echo=True) -> int:
    _launchctl("bootout", f"gui/{os.getuid()}/{AGENT_LABEL}")
    _launchctl("unload", "-w", AGENT_PLIST)
    if os.path.exists(AGENT_PLIST):
        os.remove(AGENT_PLIST)
    if echo:
        print("해제 완료 — 자동 감시가 중지됩니다.")
    return 0


def status() -> dict:
    cfg = load_config()
    pid = agent_pid()
    return {
        "agent_installed": agent_installed(),
        "agent_running": pid is not None,
        "pid": pid,
        "paused": is_paused(),
        "folders": [os.path.expanduser(f) for f in cfg["folders"]],
        "exist": [os.path.isdir(os.path.expanduser(f)) for f in cfg["folders"]],
        "quiet": cfg["quiet"],
        "stable": cfg["stable"],
        "allow_projects": cfg.get("allow_projects", False),
        "log": log_path(),
        "config": CONFIG_PATH,
    }


def run(folders=None, quiet=None, stable=None, foreground=True,
        dry_run=False, allow_projects=False, echo=True) -> int:
    cfg = load_config()
    folders = folders or cfg["folders"]
    quiet = cfg["quiet"] if quiet is None else quiet
    stable = cfg["stable"] if stable is None else stable

    resolved = [os.path.abspath(os.path.expanduser(f)) for f in folders]
    existing = [f for f in resolved if os.path.isdir(f)]

    log = Logger(echo=echo)
    missing = [f for f in resolved if f not in existing]
    log(f"감시 시작 — {', '.join(existing) if existing else '(없음)'}")
    if missing:
        log(f"없는 폴더 무시: {', '.join(missing)}")
    if not existing:
        log("감시할 폴더가 없습니다.")
        return 2

    # 권한 확인 — TCC 가 대기 상태면 FSEvents 생성이 영원히 멈춘다.
    blocked, denied = [], []
    for f in existing:
        st = probe_access(f)
        if st is None:
            blocked.append(f)
        elif st is False:
            denied.append(f)
    if blocked or denied:
        who = ", ".join(os.path.basename(p) for p in (blocked + denied))
        log(f"권한 필요 — {who}")
        log("macOS 가 '폴더 접근 허용'을 물으면 허용을 눌러 주세요. "
            "창이 안 보이면 시스템 설정 > 개인정보 보호 및 보안 > 파일 및 폴더 에서 HanFix 를 켜세요.")
        if len(blocked) + len(denied) == len(existing):
            return 3
        existing = [f for f in existing if f not in blocked and f not in denied]

    fixer = AutoFixer(existing, quiet=quiet, stable=stable, log=log,
                      allow_projects=allow_projects, dry_run=dry_run)
    log("FSEvents 스트림 생성 중")
    try:
        watcher = FSWatcher(existing, fixer.feed)
    except Exception as ex:
        log(f"FSEvents 실패: {type(ex).__name__}: {ex}")
        raise
    log(f"준비됨 (quiet={quiet}s, stable={stable}s, dry_run={dry_run})")

    fixer.catch_up()

    if not foreground:
        # 부모(앱)가 사라지면 고아가 되므로 스스로 종료한다.
        def _watch_parent():
            ppid0 = os.getppid()
            while True:
                time.sleep(2)
                if os.getppid() != ppid0:
                    log("부모 프로세스 종료 — 감시자 종료")
                    os._exit(0)
        threading.Thread(target=_watch_parent, daemon=True).start()

    try:
        while True:
            time.sleep(1.0)
            if is_paused():
                continue
    except KeyboardInterrupt:
        log("중단")
    finally:
        watcher.stop()

    log(f"종료 — 정리 {fixer.stats['renamed']} · 충돌 {fixer.stats['skipped']}")
    return 0
