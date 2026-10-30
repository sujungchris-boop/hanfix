"""
hanfix.core — macOS 한글 자소분리(NFD) 문제 해결 엔진

설계 근거(이 머신에서 실측):
  * APFS는 정규화 보존(preserving) + 정규화 무시(insensitive).
    → NFC 이름으로 rename하면 디스크에 NFC로 남고, inode도 유지된다.
    → HFS+처럼 NFD로 되돌리지 않는다.
  * /usr/bin/zip, ditto(=Finder 압축)는 NFD 이름을 UTF-8 플래그 없이 저장한다.
    → 윈도우에서 (a) 자소분리 또는 (b) CP949 오독으로 깨짐.
  * 이름을 NFC로 바꾸고 UTF-8 플래그(0x800)를 세우면 윈도우/리눅스에서 정상.
  * 파일 "내용"은 절대 건드리지 않는다. 이름만 다룬다.
"""

from __future__ import annotations

import errno
import json
import os
import stat as statmod
import subprocess
import sys
import time
import unicodedata
import uuid
import zipfile

HOME = os.path.expanduser("~")
JOURNAL_DIR = os.path.join(HOME, ".hanfix")
JOURNAL_PATH = os.path.join(JOURNAL_DIR, "journal.jsonl")

# 확장자만 보고 "내부를 건드리면 안 되는 패키지 디렉터리"를 판별.
PACKAGE_EXTS = {
    ".app", ".framework", ".bundle", ".plugin", ".kext", ".pkg", ".mpkg",
    ".photoslibrary", ".photolibrary", ".imovielibrary", ".tvlibrary",
    ".fcpbundle", ".logicx", ".band", ".rtfd", ".graffle", ".sketch",
    ".key", ".pages", ".numbers", ".sparsebundle", ".xcodeproj",
    ".xcworkspace", ".playground", ".scnpackage", ".doccarchive",
    ".appex", ".qlgenerator", ".prefpane", ".mdimporter", ".saver",
}

# 절대 건드리면 안 되는 시스템 영역
PROTECTED_PREFIXES = (
    "/System", "/usr", "/bin", "/sbin", "/dev", "/private/var/db",
    "/private/var/vm", "/Library/Apple", "/cores",
)

UTF8_FLAG = 0x800

# 코드/프로젝트 폴더 — 이름을 바꾸면 작업트리·빌드가 깨지므로 기본적으로 내부를 건드리지 않는다.
PROJECT_MARKERS = {
    ".git", ".hg", ".svn", "package.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "Podfile", "Gemfile", "composer.json", "Makefile", "CMakeLists.txt",
    "tsconfig.json", "build.gradle", "pom.xml", "requirements.txt",
}
PROJECT_SUFFIXES = (".xcodeproj", ".xcworkspace", ".sln", ".csproj", ".gradle")

# 다운로드 중인 파일 — 이름을 바꾸면 브라우저가 마무리하지 못한다.
PARTIAL_EXTS = {
    ".crdownload", ".download", ".opdownload", ".part", ".partial",
    ".tmp", ".temp", ".filepart", ".!ut", ".aria2", ".td", ".bc!",
}


# ────────────────────────────────────────────────────────────── 정규화 기본기

def nfc(s: str) -> str:
    """NFC(조합형) — 한글 음절 하나로 합친다. 호환 문자는 바꾸지 않는다(NFKC 금지)."""
    return unicodedata.normalize("NFC", s)


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def is_nfc(s: str) -> bool:
    return nfc(s) == s


def jamo_info(s: str) -> tuple[int, int]:
    """(NFC 길이, NFD 길이) — 분해된 이름일수록 격차가 크다."""
    return len(nfc(s)), len(nfd(s))


# ────────────────────────────────────────────────────────────── 볼륨 판별

class Volume:
    """마운트 지점의 파일시스템 종류 + 'NFC 이름을 실제로 저장할 수 있는가'."""

    def __init__(self, mount: str, fstype: str, nfc_capable, probe_note: str = ""):
        self.mount = mount
        self.fstype = fstype
        self.nfc_capable = nfc_capable      # True / False / None(판별 불가)
        self.probe_note = probe_note

    @property
    def forced_nfd(self) -> bool:
        return self.nfc_capable is False

    @property
    def network(self) -> bool:
        return self.fstype in ("smbfs", "nfs", "afpfs", "webdav", "cifs")

    def as_dict(self) -> dict:
        return {
            "mount": self.mount,
            "fstype": self.fstype,
            "nfc_capable": self.nfc_capable,
            "network": self.network,
            "note": self.probe_note,
        }

    @property
    def note(self) -> str:
        if self.forced_nfd:
            return f"{self.fstype} 볼륨은 파일명을 NFD로 강제 저장합니다. 이름 변경이 효과 없습니다."
        if self.network:
            return f"{self.fstype} 네트워크 볼륨 — 서버 쪽 저장 형태가 우선입니다. 변경 후 재확인하세요."
        if self.fstype in ("exfat", "msdos", "fat32"):
            return f"{self.fstype} 볼륨은 NFD/NFC를 다른 이름으로 취급합니다. 외부 반출용이면 NFC 저장이 유리합니다."
        if self.fstype == "apfs":
            return "apfs — NFC 이름이 그대로 저장됩니다(정상)."
        return ""


def _mount_table() -> list[tuple[str, str]]:
    """[(mountpoint, fstype), ...] — 마운트 테이블 파싱."""
    out = []
    try:
        text = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return out
    for line in text.splitlines():
        # /dev/disk3s1 on /Volumes/Dev (apfs, local, journaled)
        if " on " not in line or "(" not in line:
            continue
        try:
            left, right = line.split(" on ", 1)
            path = right.split(" (")[0]
            tail = right.split("(", 1)[1]
            fstype = tail.split(",")[0].strip().rstrip(")")
        except Exception:
            continue
        out.append((path, fstype))
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


def volume_of(path: str, probe: bool = True) -> Volume:
    """경로가 속한 볼륨을 찾고, 그 볼륨이 NFC 이름을 보존하는지 실측한다."""
    real = os.path.realpath(path)
    mount, fstype = "/", "unknown"
    for mp, fs in _mount_table():
        if real == mp or real.startswith(mp.rstrip("/") + "/"):
            mount, fstype = mp, fs
            break

    nfc_capable = None
    note = ""
    if probe and fstype not in ("devfs", "autofs"):
        probe_dir = real if os.path.isdir(real) else os.path.dirname(real)
        try:
            marker = "hanfixprobe\uD55C\uAE00"  # 한글
            tmp_name = nfc("." + marker + uuid.uuid4().hex[:6] + ".tmp")
            tmp_path = os.path.join(probe_dir, tmp_name)
            with open(tmp_path, "w") as f:
                f.write("probe")
            try:
                back = [n for n in os.listdir(probe_dir) if n.endswith(".tmp") and "hanfixprobe" in nfc(n)]
                nfc_capable = any(is_nfc(n) for n in back)
                if not nfc_capable:
                    note = "NFC 이름을 저장했지만 NFD로 되돌아옴"
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except OSError as e:
            note = f"쓰기 불가({e.strerror}) — 볼륨 특성 판별 생략"

    return Volume(mount, fstype, nfc_capable, note)


# ────────────────────────────────────────────────────────────── 스캔

class Entry:
    __slots__ = ("path", "rel", "old", "new", "kind", "depth", "action", "note", "dev", "ino")

    def __init__(self, path, rel, name, kind, depth):
        self.path = path
        self.rel = rel
        self.old = name
        self.new = nfc(name)
        self.kind = kind
        self.depth = depth
        self.action = "keep"      # keep | rename | skip
        self.note = ""
        self.dev = None
        self.ino = None

    @property
    def changes(self) -> bool:
        return self.old != self.new

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "rel": self.rel,
            "old": self.old,
            "new": self.new,
            "kind": self.kind,
            "action": self.action,
            "note": self.note,
        }


def is_project_dir(path: str) -> bool:
    """그 폴더가 코드 프로젝트/저장소인가(마커 파일 존재)."""
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    return bool(names & PROJECT_MARKERS) or any(n.endswith(PROJECT_SUFFIXES) for n in names)


def _is_protected(path: str) -> bool:
    rp = os.path.realpath(path)
    return any(rp == p or rp.startswith(p + "/") for p in PROTECTED_PREFIXES)


def _skip_dir_inside(name: str, root: str, path: str, skip_library: bool) -> bool:
    ext = os.path.splitext(name)[1].lower()
    if ext in PACKAGE_EXTS:
        return True
    if name == ".git":
        return True
    if skip_library:
        lib = os.path.join(HOME, "Library")
        if os.path.realpath(path) == lib:
            return True
    return False


def scan(paths, skip_library=True, follow_links=False, skip_protected=True,
         include_hidden=True, skip_projects=True) -> dict:
    """경로들을 훑어 NFD 이름을 찾는다. 아무것도 바꾸지 않는다."""
    entries: list[Entry] = []
    errors: list[dict] = []
    roots = [os.path.abspath(os.path.expanduser(p)) for p in paths]

    for root in roots:
        if not os.path.lexists(root):
            errors.append({"path": root, "error": "경로 없음"})
            continue
        if skip_protected and _is_protected(root):
            errors.append({"path": root, "error": "시스템 보호 영역 — 건너뜀"})
            continue
        if os.path.isfile(root) or os.path.islink(root):
            parent, name = os.path.split(root)
            try:
                st = os.lstat(root)
            except OSError as e:
                errors.append({"path": root, "error": e.strerror})
                continue
            e_ = Entry(root, name, name, "link" if os.path.islink(root) else "file", 0)
            e_.dev, e_.ino = st.st_dev, st.st_ino
            entries.append(e_)
            continue

        for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                    followlinks=follow_links,
                                                    onerror=lambda e: errors.append(
                                                        {"path": getattr(e, "filename", ""),
                                                         "error": e.strerror})):
            if skip_protected and _is_protected(dirpath):
                dirnames[:] = []
                continue
            depth = dirpath[len(root):].count(os.sep)
            kept = []
            for d in sorted(dirnames):
                full = os.path.join(dirpath, d)
                rel = os.path.relpath(full, root)
                if d.startswith(".") and not include_hidden and d not in (".",):
                    continue
                try:
                    st = os.lstat(full)
                except OSError as e:
                    errors.append({"path": full, "error": e.strerror})
                    continue
                ent = Entry(full, rel, d, "dir", depth + 1)
                ent.dev, ent.ino = st.st_dev, st.st_ino
                entries.append(ent)
                if _skip_dir_inside(d, root, full, skip_library):
                    continue     # 이름만 정리하고 내부는 들어가지 않음
                if skip_projects and is_project_dir(full):
                    ent.note = "코드/프로젝트 폴더 — 이름만 정리"
                    continue     # 내부 파일은 건드리지 않는다(작업트리 보호)
                kept.append(d)
            dirnames[:] = kept

            for f in sorted(filenames):
                full = os.path.join(dirpath, f)
                if f == ".DS_Store":
                    continue
                try:
                    st = os.lstat(full)
                except OSError as e:
                    errors.append({"path": full, "error": e.strerror})
                    continue
                kind = "link" if statmod.S_ISLNK(st.st_mode) else "file"
                ent = Entry(full, os.path.relpath(full, root), f, kind, depth + 1)
                ent.dev, ent.ino = st.st_dev, st.st_ino
                entries.append(ent)

    _resolve_actions(entries)

    nfd_count = sum(1 for e in entries if e.changes)
    return {
        "roots": roots,
        "volume": volume_of(roots[0]).as_dict() if roots else None,
        "counts": {
            "checked": len(entries),
            "nfd": nfd_count,
            "rename": sum(1 for e in entries if e.action == "rename"),
            "skip": sum(1 for e in entries if e.action == "skip"),
            "clean": len(entries) - nfd_count,
        },
        "entries": entries,
        "errors": errors,
    }


# ────────────────────────────────────────────────────────────── 실행 / 되돌리기

def _resolve_actions(entries) -> None:
    """각 항목의 action(rename/skip/keep)을 정한다.

    APFS는 정규화를 무시하므로 '대상이 존재한다'는 것만으로는 충돌이 아니다.
    같은 inode면 같은 파일의 별칭이므로 rename해도 안전하다.
    """
    by_dir: dict[str, set] = {}
    for e in entries:
        if e.changes:
            continue
        by_dir.setdefault(os.path.dirname(e.path), set()).add(e.old)

    for e in entries:
        if not e.changes:
            e.action = "keep"
            continue
        taken = by_dir.get(os.path.dirname(e.path), set())
        if e.new in taken:
            e.action = "skip"
            e.note = "같은 이름의 다른 항목이 이미 존재 — 건너뜀"
            continue
        target = os.path.join(os.path.dirname(e.path), e.new)
        if os.path.lexists(target):
            try:
                st = os.lstat(target)
                if (st.st_dev, st.st_ino) == (e.dev, e.ino):
                    e.action = "rename"   # 정규화 별칭 → 같은 파일. 안전.
                else:
                    e.action = "skip"
                    e.note = "다른 파일이 그 이름을 사용 중 — 건너뜀"
            except OSError:
                e.action = "skip"
                e.note = "대상 확인 실패"
        else:
            e.action = "rename"


def plan_paths(paths):
    """명시된 경로들만 대상으로 Entry 목록을 만든다(감시 데몬용)."""
    entries = []
    for p in paths:
        p = os.path.abspath(p)
        name = os.path.basename(p.rstrip("/"))
        if not name:
            continue
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if statmod.S_ISLNK(st.st_mode):
            kind = "link"
        elif statmod.S_ISDIR(st.st_mode):
            kind = "dir"
        else:
            kind = "file"
        e = Entry(p, name, name, kind, p.count(os.sep))
        e.dev, e.ino = st.st_dev, st.st_ino
        entries.append(e)
    _resolve_actions(entries)
    return entries


def _journal_append(records: list[dict]) -> None:
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def apply_fix(entries, dry_run=False) -> dict:
    """scan()이 만든 Entry 목록을 실제로 rename한다. 깊은 경로부터 처리."""
    batch = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    todo = [e for e in entries if e.action == "rename"]
    # 깊은 곳부터: 부모 이름이 바뀌어도 자식 절대경로가 유효하도록.
    todo.sort(key=lambda e: e.depth, reverse=True)

    done, failed, records = [], [], []
    for e in todo:
        target = os.path.join(os.path.dirname(e.path), e.new)
        if dry_run:
            done.append({"old": e.path, "new": target})
            continue
        try:
            if os.path.lexists(target):
                st = os.lstat(target)
                if (st.st_dev, st.st_ino) != (e.dev, e.ino):
                    failed.append({"path": e.path, "error": "대상 이름 선점됨"})
                    continue
            os.rename(e.path, target)
        except OSError as ex:
            failed.append({"path": e.path, "error": ex.strerror or str(ex)})
            continue

        # 검증: 디스크에 정말 NFC로 들어갔는가
        parent, want = os.path.dirname(target), e.new
        ok = False
        try:
            for n in os.listdir(parent):
                if n == want:
                    ok = True
                    break
        except OSError:
            pass
        if not ok:
            failed.append({"path": e.path, "error": "변경 후 확인 실패"})
            continue

        done.append({"old": e.path, "new": target})
        records.append({
            "ts": time.time(), "batch": batch, "op": "rename",
            "from": e.path, "to": target, "dev": e.dev, "ino": e.ino,
        })

    if records:
        _journal_append(records)

    return {"batch": batch, "renamed": len(done), "failed": failed, "items": done,
            "dry_run": dry_run}


def undo(count_batches: int = 1) -> dict:
    """마지막 배치(들)를 역순으로 되돌린다."""
    if not os.path.exists(JOURNAL_PATH):
        return {"restored": 0, "failed": [], "note": "저널 없음"}
    with open(JOURNAL_PATH, encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    if not lines:
        return {"restored": 0, "failed": [], "note": "저널 비어 있음"}

    batches: list[str] = []
    for r in reversed(lines):
        b = r.get("batch")
        if b not in batches:
            batches.append(b)
            if len(batches) >= count_batches:
                break
    targets = [r for r in lines if r.get("batch") in batches]

    restored, failed, used = 0, [], set()
    for r in reversed(targets):
        src, dst = r["to"], r["from"]
        try:
            st = os.lstat(src)
        except OSError:
            failed.append({"path": src, "error": "원본이 사라짐"})
            continue
        if (st.st_dev, st.st_ino) != (r.get("dev"), r.get("ino")):
            failed.append({"path": src, "error": "inode 불일치 — 다른 파일, 되돌리지 않음"})
            continue
        if os.path.lexists(dst):
            try:
                st2 = os.lstat(dst)
                if (st2.st_dev, st2.st_ino) != (r.get("dev"), r.get("ino")):
                    failed.append({"path": dst, "error": "되돌릴 이름을 다른 파일이 사용 중"})
                    continue
            except OSError:
                pass
        try:
            os.rename(src, dst)
            restored += 1
            used.add(id(r))
        except OSError as ex:
            failed.append({"path": src, "error": ex.strerror})

    if restored:
        keep = [r for r in lines if r not in targets]
        with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return {"restored": restored, "failed": failed, "batches": batches}


# ────────────────────────────────────────────────────────────── ZIP 생성

def create_zip(sources, out_path, include_dsstore=False, include_macosx=False,
               show_progress=None) -> dict:
    """
    NFC 이름 + UTF-8 플래그로 zip을 만든다.
    맥 기본 압축(ditto)의 두 결함(① NFD 저장 ② 플래그 미설정)을 동시에 제거.
    """
    sources = [os.path.abspath(os.path.expanduser(s)) for s in sources]
    out_path = os.path.abspath(os.path.expanduser(out_path))
    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(out_path):
        base, ext = os.path.splitext(out_path)
        i = 1
        while os.path.exists(f"{base} ({i}){ext}"):
            i += 1
        out_path = f"{base} ({i}){ext}"

    added, skipped, total_bytes, fixed_names = 0, [], 0, 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:

        def add(path, arcname):
            nonlocal added, total_bytes, fixed_names
            arc = nfc(arcname)
            if arc != arcname:
                fixed_names += 1
            try:
                z.write(path, arc)
                added += 1
                try:
                    total_bytes += os.path.getsize(path)
                    if show_progress and added % 25 == 0:
                        show_progress(added, arc)
                except OSError:
                    pass
            except OSError as e:
                skipped.append({"path": path, "error": e.strerror or str(e)})

        for src in sources:
            if not os.path.exists(src):
                skipped.append({"path": src, "error": "경로 없음"})
                continue
            if os.path.basename(src) in (".DS_Store", "__MACOSX") and not include_dsstore:
                continue
            if os.path.isfile(src):
                add(src, os.path.basename(src))
                continue
            root_name = os.path.basename(src.rstrip("/")) or "archive"
            # 디렉터리 자체 엔트리
            try:
                z.write(src, nfc(root_name) + "/")
                added += 1
            except OSError as e:
                skipped.append({"path": src, "error": e.strerror or str(e)})
            for dirpath, dirnames, filenames in os.walk(src):
                if not include_macosx and "__MACOSX" in dirnames:
                    dirnames.remove("__MACOSX")
                dirnames.sort()
                for d in list(dirnames):
                    full = os.path.join(dirpath, d)
                    rel = os.path.relpath(full, src)
                    arc = os.path.join(root_name, rel) + "/"
                    try:
                        z.write(full, nfc(arc))
                        added += 1
                    except OSError as e:
                        skipped.append({"path": full, "error": e.strerror or str(e)})
                for f in sorted(filenames):
                    if not include_dsstore and f == ".DS_Store":
                        continue
                    full = os.path.join(dirpath, f)
                    rel = os.path.relpath(full, src)
                    add(full, os.path.join(root_name, rel))

    # 검증: UTF-8 플래그와 NFC 여부를 실제로 다시 읽어서 확인
    bad_flag, bad_nfc = 0, 0
    with zipfile.ZipFile(out_path) as z:
        for i in z.infolist():
            if not is_nfc(i.filename):
                bad_nfc += 1
            if not (i.flag_bits & UTF8_FLAG) and any(ord(c) > 127 for c in i.filename):
                bad_flag += 1

    return {
        "out": out_path,
        "entries": added,
        "names_normalized": fixed_names,
        "bytes": total_bytes,
        "size": os.path.getsize(out_path),
        "skipped": skipped,
        "verify": {"non_nfc": bad_nfc, "missing_utf8_flag": bad_flag},
    }


# ────────────────────────────────────────────────────────────── ZIP 해제

def _decode_entry_name(info: zipfile.ZipInfo) -> str:
    """플래그 없으면 zipfile이 cp437로 디코드한다 → 원바이트 복원 후 UTF-8/CP949 시도."""
    if info.flag_bits & UTF8_FLAG:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    if all(b < 0x80 for b in raw):
        return info.filename
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _safe_relpath(name: str) -> str | None:
    """zip 안의 이름을 안전한 상대경로로. 절대경로·상위참조는 거부."""
    name = name.replace("\\", "/")
    if name.startswith("/") or name.startswith("~"):
        return None
    if len(name) > 1 and name[1] == ":":        # C:\... 류
        return None
    parts = name.split("/")
    if any(p == ".." for p in parts):
        return None
    parts = [p for p in parts if p not in ("", ".")]
    if not parts:
        return None
    return "/".join(parts)


def extract_zip(zip_path, dest=None, overwrite=False) -> dict:
    zip_path = os.path.abspath(os.path.expanduser(zip_path))
    if dest is None:
        stem = nfc(os.path.splitext(os.path.basename(zip_path))[0])
        dest = os.path.join(os.path.dirname(zip_path), stem)
    dest = os.path.abspath(os.path.expanduser(dest))
    os.makedirs(dest, exist_ok=True)

    written, repaired, skipped, errors = 0, 0, [], []
    names_before, names_after = [], []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            raw_name = _decode_entry_name(info)
            names_before.append(raw_name)
            fixed = nfc(raw_name)
            rel = _safe_relpath(fixed)
            if rel is None:
                skipped.append({"entry": info.filename, "reason": "안전하지 않은 경로"})
                continue
            if fixed != raw_name:
                repaired += 1
            names_after.append(fixed)
            target = os.path.join(dest, rel)

            is_dir = info.is_dir() or fixed.endswith("/")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:          # 심볼릭 링크
                link_target = z.read(info).decode("utf-8", "replace")
                try:
                    if os.path.lexists(target):
                        os.unlink(target)
                    os.symlink(link_target, target)
                except OSError as e:
                    errors.append({"entry": fixed, "error": e.strerror})
                continue
            if is_dir:
                os.makedirs(target, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.exists(target) and not overwrite:
                skipped.append({"entry": fixed, "reason": "이미 존재"})
                continue
            try:
                with z.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        dst.write(chunk)
                written += 1
                try:  # 원본 수정시각 보존
                    import datetime
                    dt = datetime.datetime(*info.date_time)
                    ts = dt.timestamp()
                    os.utime(target, (ts, ts))
                except Exception:
                    pass
            except OSError as e:
                errors.append({"entry": fixed, "error": e.strerror or str(e)})

    return {
        "zip": zip_path,
        "dest": dest,
        "files": written,
        "names_repaired": repaired,
        "skipped": skipped,
        "errors": errors,
        "non_nfc_left": sum(1 for n in names_after if not is_nfc(n)),
    }


def check_zip(zip_path) -> dict:
    """zip이 윈도우에서 안전한지 검사(이름 정규형 + 플래그)."""
    zip_path = os.path.abspath(os.path.expanduser(zip_path))
    bad_nfc, bad_flag, cp949 = [], [], []
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            total += 1
            name = _decode_entry_name(info)
            if not is_nfc(name):
                bad_nfc.append(name)
            if not (info.flag_bits & UTF8_FLAG) and any(ord(c) > 127 for c in name):
                bad_flag.append(name)
            try:
                raw = info.filename.encode("cp437")
                try:
                    raw.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        raw.decode("cp949")
                        cp949.append(name)
                    except UnicodeDecodeError:
                        pass
            except UnicodeEncodeError:
                pass
    return {
        "zip": zip_path,
        "entries": total,
        "non_nfc": bad_nfc[:50],
        "non_nfc_count": len(bad_nfc),
        "missing_utf8_flag_count": len(bad_flag),
        "cp949_count": len(cp949),
        "safe": not bad_nfc and not bad_flag,
    }
