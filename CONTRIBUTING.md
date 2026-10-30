# Contributing

Thanks for helping. This project is small on purpose — please keep changes focused.

## Before you start

- The core logic is Python (stdlib only, no dependencies). The GUI is SwiftUI compiled
  with `swiftc` — there is no Xcode project, and adding one is discouraged.
- Every filesystem change must be: idempotent, journaled, and reversible.
- If you fix a normalization or archive edge case, add a test that fails before your
  fix. `tests/test_hanfix.py` and `tests/test_watch.py` run against a real APFS volume.

## Setup

```bash
git clone https://github.com/<owner>/hanfix && cd hanfix
./scripts/fetch_runtime.sh     # only needed for building the .app
./scripts/build.sh
python3 tests/test_hanfix.py
python3 tests/test_watch.py
```

## Ground rules

- **Never touch file contents.** Renames only.
- **Never** skip the inode check on rename collisions. APFS resolves both
  normalization forms to the same file, so a plain `exists()` test is wrong.
- Do not use NFKC. It rewrites compatibility characters and changes what the user sees.
- Do not add network calls. The tool is offline by design.
- Keep the CLI as the single implementation; the GUI must call it rather than
  duplicate logic.

## Reporting bugs

Include: macOS version, `hanfix watch --status` output if relevant, the last 20 lines
of `~/.hanfix/watch.log`, and the exact filename bytes (`python3 -c "import
unicodedata as u; print([hex(ord(c)) for c in '이름'])"`).
