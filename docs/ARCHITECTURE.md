# Architecture

## Layout

```
HanFix.app/
  Contents/MacOS/HanFix              universal SwiftUI app (arm64 + x86_64)
  Contents/Resources/hanfix/         Python core (cli.py, core.py, watch.py)
  Contents/Resources/runtime/<arch>/ bundled CPython 3.12
```

The app resolves paths from `Bundle.main`, so it works from `/Applications`, from a
mounted DMG, or from a build directory — nothing is hardcoded.

## Two entry points, one core

- **GUI** — SwiftUI. All filesystem work is delegated to the CLI via `Process`,
  and results come back as JSON. The Swift side never renames anything itself.
- **CLI** — `hanfix <command>`. The same code path the app uses, so bugs cannot
  hide in one surface only.

## Why the watcher is started by the app, not by launchd

macOS TCC protects `~/Desktop`, `~/Documents` and `~/Downloads`. A LaunchAgent that
runs `/usr/bin/python3` directly gets `Operation not permitted` on `os.listdir()`
even though FSEvents still delivers paths (measured on macOS 26/27).

The fix: the LaunchAgent launches `HanFix.app --watch-daemon`, and the app spawns
the Python watcher as a child. A child process inherits the parent's TCC
responsibility, so folder access works without asking the user to grant
Full Disk Access.

Consequences that must be preserved:

- `applicationShouldTerminateAfterLastWindowClosed` must return `false` in daemon
  mode, otherwise hiding the window terminates the job and launchd loops.
- `applicationShouldHandleReopen` must show the window, otherwise clicking the app
  icon while the daemon runs does nothing visible.
- The child watches `os.getppid()` and exits when the parent disappears, so no
  orphan watchers survive an app crash.

## Watcher policy

| Concern | Handling |
|---|---|
| Event source | FSEvents via `ctypes` (`FSEventStreamCreate` + dispatch queue). The callback object must be kept referenced or the process segfaults. |
| Startup gap | FSEvents only reports events after the stream starts; `catch_up()` sweeps files modified in the last 5 minutes on boot. |
| Debounce | Events are batched for `quiet` seconds, then files must be unchanged for `stable` seconds (in-progress downloads). |
| Rename loop | APFS is normalization-insensitive, so `lexists(nfd_path)` stays true forever. The watcher matches names exactly against `listdir(parent)`, plus a short blacklist of recently renamed paths. |
| Project safety | Directories containing `.git`, `package.json`, `*.xcodeproj`, … are never entered. |
| Collisions | Target must be the same inode, otherwise the item is skipped and logged. |

## Journal & undo

Every rename is appended to `~/.hanfix/journal.jsonl` as
`{ts, batch, op, from, to, dev, ino}`. `undo` walks the last batch in reverse and
re-verifies `(dev, ino)` before each rename, so a file that has been replaced in the
meantime is never clobbered.

## Building

`scripts/build.sh` compiles both architectures with `swiftc`, `lipo`s them, copies
the Python core and the bundled runtime, ad-hoc signs nested binaries first and the
bundle last. There is no Xcode project file on purpose: `swiftc` is enough and the
build stays reproducible in CI.

`scripts/fetch_runtime.sh` downloads CPython from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) and
trims tests/IDLE/tkinter/pip. `vendor/` is not committed.
