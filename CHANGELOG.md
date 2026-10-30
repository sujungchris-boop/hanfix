# Changelog

## 1.1.0 — 2026-10-30

- **Settings window** (⌘,): choose watched folders, toggle auto-start, project
  protection and timing without touching the command line.
- **Self-contained app**: bundled CPython runtime, universal binary (arm64 + x86_64).
  No Python installation required.
- **DMG installer** with drag-to-Applications, plus a GitHub Actions release workflow.
- **Startup catch-up**: files created in the few seconds while the watcher boots are
  no longer missed.
- Project folders (git, Xcode, npm …) are now protected during manual bulk renames
  too, not only in watch mode.
- English and Korean documentation.

## 1.0.0 — 2026-10-26

- Initial release: NFD → NFC renaming with inode-safe collision checks and undo,
  Windows-safe zip creation (NFC names + UTF-8 flag), zip extraction with CP949/NFD
  name repair, FSEvents-based folder watching via LaunchAgent.
