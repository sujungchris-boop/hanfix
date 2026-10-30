# HanFix

**Fix Korean filenames broken between macOS and Windows (NFD ↔ NFC) — for files and for zip archives.**

[한국어 문서](README.ko.md)

macOS stores Hangul filenames in decomposed form (NFD), so `한글.pdf` arrives as
`ㅎㅏㄴㄱㅡㄹ.pdf` on Windows, on a NAS, or in a web upload form. HanFix renames them
to composed form (NFC). Because macOS's own *Compress* also writes names without a
UTF-8 flag, HanFix can additionally build zip archives that open correctly on Windows.

![HanFix main window](docs/screenshot.png)

## Why this exists

Three separate things break Korean filenames, and most tools fix only one of them.

| # | Where | What actually happens |
|---|---|---|
| 1 | **Normalization** | macOS writes `한글` as 12 code points (U+1112 U+1161 U+11AB …); Windows, Linux and the web expect 5. |
| 2 | **APFS** | It is normalization-*preserving* and normalization-*insensitive*: what you write is what stays, and both spellings resolve to the same file. So renaming to NFC **really does fix it**, and keeps the inode. |
| 3 | **Archives** | `/usr/bin/zip` and Finder's *Compress* (`ditto`) copy on-disk bytes as-is **and never set the UTF-8 flag (0x800)**. Windows Explorer then decodes the names as CP949 → mojibake. |

A complete tool has to handle filenames *and* the packing step — and refuse the
cases it cannot fix (HFS+, SMB/NAS) instead of pretending.

## Install

1. Download the latest `HanFix-x.y.z.dmg` from [Releases](../../releases).
2. Drag **HanFix.app** onto **Applications**.
3. First launch: right-click the app → **Open** → **Open** (ad-hoc signed, so Gatekeeper asks once).

No Python, no Homebrew, no dependencies — the runtime ships inside the app.
Requires macOS 13+ (Apple Silicon or Intel).

## Use

Drag a folder, a file or a `.zip` onto the window.

- **전체 이름 정리** — rename NFD → NFC (preview list first; undo available)
- **윈도우용 압축** — build a zip with NFC names **and** the UTF-8 flag
- **압축 풀기** — extract while repairing CP949/NFD names inside the archive
- **되돌리기** — undo the last batch (inode-verified)

### Always-on watching

macOS only protects three folders: `~/Downloads`, `~/Desktop` and `~/Documents`.
HanFix needs your approval once for those — allow the prompts, or add the app under
**System Settings → Privacy & Security → Full Disk Access**. Without it the watcher logs
`권한 필요` and does nothing (it will not touch your files either).

**Any other folder** (e.g. `~/work`, an external drive) is not TCC-protected and is
watched immediately, with no permission at all. If you keep your documents somewhere
like `~/제안서`, point HanFix there and skip the permission entirely.

Because the app is ad-hoc signed, replacing it with a new version gives it a new
signature and macOS asks for the folder permission again. Re-enable it once after
updating.

Open **설정 (⌘,)** and choose folders — Downloads, Desktop and Documents by default.
New or moved-in files are normalized within a few seconds, in the background, with
a menu bar item so you can see it is alive.

![Settings](docs/settings.png)

Safety rules in watch mode:

- names only — contents, extension and timestamps are never modified
- in-progress downloads (`.crdownload`, `.part`, `.download`, …) are skipped
- files still being written are retried until they settle (2 s by default)
- git repositories and code projects (`package.json`, `*.xcodeproj`, …) are protected
- `.app` bundles and hidden system folders are never entered
- every change is journaled, so `hanfix undo` also reverts automatic fixes

## What it cannot fix

- **HFS+ volumes** — the filesystem forces NFD on write. HanFix detects this and refuses.
- **SMB / NAS** — the macOS client converts names to NFC on the wire, so NFD files
  already on the server can be listed but not opened (error -43). Fix them *on the server*.
- **Inside existing archives** — use `hanfix unzip` / `hanfix zip` instead.

## CLI

```bash
H="/Applications/HanFix.app/Contents/Resources/hanfix/bin/hanfix"

$H scan  ~/Downloads                  # report only, changes nothing
$H fix   ~/Downloads                  # dry run (default)
$H fix   ~/Downloads --apply          # rename NFD → NFC
$H undo                               # revert last batch
$H zip   ~/Downloads -o out.zip       # Windows-safe zip
$H unzip download.zip                 # extract with names repaired
$H zipcheck download.zip              # is this zip safe for Windows?
$H watch --status                     # is the watcher running?
$H watch --install ~/Downloads ~/문서  # choose watched folders
$H watch --pause | --resume | --uninstall
```

Every command accepts `--json`. Exit codes: `0` clean, `1` issues found, `2` volume cannot be fixed.

## How it is built

```
app/            SwiftUI front end (universal binary, no Xcode project needed)
hanfix/core.py  normalization, volume probing, safe rename, journal, zip
hanfix/watch.py FSEvents watcher + policy (debounce, stability, project guards)
hanfix/cli.py   argparse CLI — the same entry point the app calls
vendor/         bundled CPython (scripts/fetch_runtime.sh; not committed)
```

The watcher is started by a LaunchAgent that launches **HanFix.app** with
`--watch-daemon`; the app then spawns the Python watcher as a child. That indirection
is deliberate: launchd cannot read `~/Downloads` (TCC blocks background processes),
but a child process of the app inherits the app's permission. Details in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Build from source

```bash
./scripts/fetch_runtime.sh     # one-time: bundled Python (~105 MB)
./scripts/build.sh             # → build/HanFix.app (universal)
./scripts/build_dmg.sh         # → build/HanFix-x.y.z.dmg
python3 tests/test_hanfix.py   # 13 unit tests
python3 tests/test_watch.py    # 6 watcher integration tests
```

Requires Xcode Command Line Tools (for `swiftc`).

## Privacy

No network access, no telemetry, no analytics. Everything happens locally on your
files, and only in the folders you choose.

## License

MIT — see [LICENSE](LICENSE).
