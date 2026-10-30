#!/usr/bin/env python3
"""hanfix CLI — 맥 한글 자소분리(NFD) 정리 도구.

  hanfix scan  <경로...>            # 무엇이 문제인지 보기만
  hanfix fix   <경로...> --apply    # 실제로 NFC로 rename (--apply 없으면 예행연습)
  hanfix undo                       # 마지막 작업 되돌리기
  hanfix zip   <경로...> -o out.zip # 윈도우 안전 zip 만들기
  hanfix unzip <a.zip> [more.zip]   # zip 풀면서 이름 복원
  hanfix zipcheck <a.zip>           # zip이 윈도우에서 안전한지 검사
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hanfix import core  # noqa: E402

BOLD, DIM, RED, GRN, YEL, CYN, RST = (
    ("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m")
    if sys.stdout.isatty() else ("",) * 7
)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def emit_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ───────────────────────────────────────────────────── 명령

def cmd_scan(args) -> int:
    res = core.scan(args.paths, skip_library=not args.include_library,
                    follow_links=args.follow_links, skip_protected=not args.no_guard)
    entries = res["entries"]
    if args.json:
        emit_json({
            "roots": res["roots"],
            "volume": res["volume"],
            "counts": res["counts"],
            "errors": res["errors"],
            "items": [e.as_dict() for e in entries if e.changes][:2000],
        })
        return 0 if res["counts"]["nfd"] == 0 else 1

    vol = res["volume"]
    print(f"{BOLD}스캔{RST} {', '.join(res['roots'])}")
    if vol:
        print(f"  볼륨: {vol['mount']} ({vol['fstype']})"
              + ("" if vol["nfc_capable"] is None else
                 f" — NFC 저장 {'가능' if vol['nfc_capable'] else '불가'}"))
        v = core.Volume(vol["mount"], vol["fstype"], vol["nfc_capable"], vol["note"])
        if v.note:
            print(f"  {DIM}{v.note}{RST}")
    c = res["counts"]
    print(f"  검사 {c['checked']}개 · 문제 {YEL}{c['nfd']}{RST}개 · 정상 {c['clean']}개"
          f" · 건너뜀 {c['skip']}개")
    for e in entries:
        if e.changes and e.action == "rename":
            a, b = core.jamo_info(e.old)
            extra = f"{DIM}({a}자 ← {b}자){RST}" if a != b else ""
            print(f"  {YEL}NFD{RST} {e.rel} {extra}")
        elif e.action == "skip":
            print(f"  {RED}skip{RST} {e.rel} — {e.note}")
    for er in res["errors"][:20]:
        print(f"  {RED}err{RST} {er['path']} — {er['error']}")
    if c["nfd"] and not args.quiet:
        print(f"\n  {DIM}정리하려면: hanfix fix {' '.join(res['roots'])} --apply{RST}")
    return 0 if c["nfd"] == 0 else 1


def cmd_fix(args) -> int:
    res = core.scan(args.paths, skip_library=not args.include_library,
                    follow_links=args.follow_links, skip_protected=not args.no_guard,
                    skip_projects=not args.allow_projects)
    vol = res["volume"]
    if vol and vol["nfc_capable"] is False and not args.force:
        msg = f"{vol['fstype']} 볼륨은 파일명을 NFD로 강제 저장합니다 — 변경해도 소용없습니다."
        if args.json:
            emit_json({"error": msg, "volume": vol})
        else:
            print(f"{RED}{msg}{RST}", file=sys.stderr)
        return 2

    out = core.apply_fix(res["entries"], dry_run=not args.apply)
    if args.json:
        emit_json({
            "applied": bool(args.apply),
            "batch": out["batch"],
            "renamed": out["renamed"],
            "failed": out["failed"],
            "volume": vol,
            "items": out["items"][:2000],
        })
        return 0 if not out["failed"] else 1

    verb = "변경" if args.apply else "변경 예정(예행연습)"
    print(f"{BOLD}{verb} {out['renamed']}개{RST}  배치 {out['batch']}")
    for it in out["items"]:
        print(f"  {GRN}→{RST} {os.path.basename(it['old'])}  ⇒  {os.path.basename(it['new'])}")
    for f in out["failed"]:
        print(f"  {RED}실패{RST} {f['path']} — {f['error']}")
    if not args.apply and out["renamed"]:
        print(f"\n  {DIM}실제로 적용: 같은 명령에 --apply{RST}")
    elif args.apply and out["renamed"]:
        print(f"  {DIM}되돌리기: hanfix undo{RST}")
    return 0 if not out["failed"] else 1


def cmd_undo(args) -> int:
    res = core.undo(args.batches)
    if args.json:
        emit_json(res)
        return 0 if not res["failed"] else 1
    print(f"{BOLD}되돌림 {res['restored']}개{RST}")
    for f in res["failed"]:
        print(f"  {RED}실패{RST} {f['path']} — {f['error']}")
    return 0 if not res["failed"] else 1


def cmd_zip(args) -> int:
    out = args.out
    if not out:
        first = os.path.basename(os.path.abspath(args.paths[0]).rstrip("/")) or "archive"
        out = core.nfc(first) + ".zip"
    res = core.create_zip(args.paths, out,
                          include_dsstore=args.all, include_macosx=args.all)
    if args.json:
        emit_json(res)
        return 0 if res["verify"]["non_nfc"] == 0 else 1
    v = res["verify"]
    print(f"{BOLD}생성{RST} {res['out']}")
    print(f"  항목 {res['entries']}개 · {human(res['size'])} · 이름 정규화 {res['names_normalized']}건")
    ok = v["non_nfc"] == 0 and v["missing_utf8_flag"] == 0
    print(f"  검증: NFD 잔존 {v['non_nfc']} · UTF-8 플래그 누락 {v['missing_utf8_flag']} "
          f"→ {GRN+'윈도우 안전'+RST if ok else RED+'문제 있음'+RST}")
    for s in res["skipped"][:10]:
        print(f"  {YEL}skip{RST} {s['path']} — {s['error']}")
    return 0 if ok else 1


def cmd_unzip(args) -> int:
    rc = 0
    allres = []
    for zp in args.zips:
        res = core.extract_zip(zp, dest=args.dest, overwrite=args.overwrite)
        allres.append(res)
        if args.json:
            continue
        print(f"{BOLD}해제{RST} {res['zip']}")
        print(f"  → {res['dest']}")
        print(f"  파일 {res['files']}개 · 이름 복원 {res['names_repaired']}건"
              f" · 건너뜀 {len(res['skipped'])}")
        if res["non_nfc_left"]:
            print(f"  {RED}NFD 잔존 {res['non_nfc_left']}건{RST}")
            rc = 1
    if args.json:
        emit_json(allres)
    return rc


def cmd_zipcheck(args) -> int:
    rc = 0
    out = []
    for zp in args.zips:
        res = core.check_zip(zp)
        out.append(res)
        if not args.json:
            flag = f"{GRN}안전{RST}" if res["safe"] else f"{RED}위험{RST}"
            print(f"{BOLD}{os.path.basename(zp)}{RST} — {flag}  (항목 {res['entries']}개)")
            if res["non_nfc_count"]:
                print(f"  NFD 이름 {res['non_nfc_count']}건 — 예: {res['non_nfc'][0]!r}")
            if res["missing_utf8_flag_count"]:
                print(f"  UTF-8 플래그 누락 {res['missing_utf8_flag_count']}건 — 윈도우에서 CP949 오독 위험")
            if res["cp949_count"]:
                print(f"  CP949 이름 {res['cp949_count']}건 — 옛 윈도우 도구로 만든 압축")
        if not res["safe"]:
            rc = 1
    if args.json:
        emit_json(out)
    return rc


def cmd_watch(args) -> int:
    from hanfix import watch as w

    if args.install:
        cfg = w.load_config()             # 폴더를 안 주면 기본값(다운로드·데스크탑·문서)
        if args.folders:
            cfg["folders"] = args.folders
        w.save_config(cfg)                # 항상 파일로 남겨 확인 가능하게
        print(f"감시 폴더: {', '.join(cfg['folders'])}")
        return w.install_agent()
    if args.uninstall:
        return w.uninstall_agent()

    if args.status:
        st = w.status()
        if args.json:
            emit_json(st)
            return 0
        print(f"{BOLD}자동 감시 상태{RST}")
        print(f"  등록: {'예' if st['agent_installed'] else '아니오'}"
              + (f" (pid {st['pid']})" if st['agent_running'] else " (중지됨)"))
        print(f"  일시정지: {'예' if st['paused'] else '아니오'}")
        for f, ok in zip(st["folders"], st["exist"]):
            print(f"  감시 폴더: {f} {'' if ok else f'{RED}(없음){RST}'}")
        print(f"  로그: {st['log']}")
        print(f"  설정: {st['config']}")
        return 0

    if args.pause:
        os.makedirs(os.path.dirname(w.PAUSE_FILE), exist_ok=True)
        open(w.PAUSE_FILE, "w").close()
        print("일시정지 — 새 이벤트를 처리하지 않습니다. (hanfix watch --resume)")
        return 0
    if args.resume:
        try:
            os.remove(w.PAUSE_FILE)
        except FileNotFoundError:
            pass
        print("재개")
        return 0

    if args.folders and args.save_folders:
        cfg = w.load_config()
        cfg["folders"] = args.folders
        w.save_config(cfg)
        print(f"감시 폴더 저장: {', '.join(cfg['folders'])}")

    return w.run(folders=args.folders, quiet=args.quiet, stable=args.stable,
                 dry_run=args.dry_run, allow_projects=args.allow_projects,
                 echo=not args.daemon)


# ───────────────────────────────────────────────────── parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hanfix",
        description="맥 한글 자소분리(NFD/NFC) 정리 — 파일명·압축파일 한 번에",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version="hanfix 1.0")
    sub = p.add_subparsers(dest="cmd", required=False)

    def common(sp):
        sp.add_argument("paths", nargs="+", help="파일 또는 폴더")
        sp.add_argument("--json", action="store_true", help="기계 판독용 JSON 출력")
        sp.add_argument("--include-library", action="store_true", help="~/Library 내부도 검사")
        sp.add_argument("--follow-links", action="store_true", help="심볼릭 링크 따라가기")
        sp.add_argument("--no-guard", action="store_true", help="시스템 보호 영역 보호 해제")

    sp = sub.add_parser("scan", help="문제 항목 찾기(변경 없음)")
    common(sp)
    sp.add_argument("--quiet", action="store_true")
    sp.add_argument("--allow-projects", action="store_true",
                    help="코드 프로젝트 내부까지 검사")
    sp.set_defaults(func=cmd_scan)

    fp = sub.add_parser("fix", help="NFC로 이름 변경")
    common(fp)
    fp.add_argument("--apply", action="store_true", help="실제 적용(없으면 예행연습)")
    fp.add_argument("--force", action="store_true", help="NFD 강제 볼륨에서도 시도")
    fp.add_argument("--allow-projects", action="store_true",
                    help="코드 프로젝트(git 등) 내부도 정리 — 기본은 보호")
    fp.set_defaults(func=cmd_fix)

    up = sub.add_parser("undo", help="마지막 변경 되돌리기")
    up.add_argument("-n", "--batches", type=int, default=1, help="되돌릴 배치 수(기본 1)")
    up.add_argument("--json", action="store_true")
    up.set_defaults(func=cmd_undo)

    zp = sub.add_parser("zip", help="윈도우 안전 zip 만들기(NFC+UTF-8 플래그)")
    zp.add_argument("paths", nargs="+")
    zp.add_argument("-o", "--out", help="출력 파일(기본: 첫 항목 이름.zip)")
    zp.add_argument("--all", action="store_true", help=".DS_Store/__MACOSX 포함")
    zp.add_argument("--json", action="store_true")
    zp.set_defaults(func=cmd_zip)

    xp = sub.add_parser("unzip", help="zip 해제(+이름 NFC 복원)")
    xp.add_argument("zips", nargs="+")
    xp.add_argument("-d", "--dest", help="해제 위치")
    xp.add_argument("--overwrite", action="store_true")
    xp.add_argument("--json", action="store_true")
    xp.set_defaults(func=cmd_unzip)

    wp = sub.add_parser("watch", help="폴더를 상시 감시하며 새 NFD 이름을 자동 정리")
    wp.add_argument("folders", nargs="*", help="감시할 폴더(지정 시 설정에 저장)")
    wp.add_argument("--install", action="store_true", help="로그인 시 자동 시작 등록(LaunchAgent)")
    wp.add_argument("--uninstall", action="store_true", help="자동 시작 해제")
    wp.add_argument("--status", action="store_true", help="상태 보기")
    wp.add_argument("--pause", action="store_true", help="일시정지")
    wp.add_argument("--resume", action="store_true", help="재개")
    wp.add_argument("--daemon", action="store_true", help="조용히 실행(로그만)")
    wp.add_argument("--fg", action="store_true", help="포그라운드 실행(설정에 저장 안 함)")
    wp.add_argument("--save-folders", action="store_true", help="주어진 폴더를 설정에 저장")
    wp.add_argument("--dry-run", action="store_true", help="실제 변경 없이 로그만")
    wp.add_argument("--allow-projects", action="store_true",
                    help="git/코드 폴더 안쪽도 정리(기본은 보호)")
    wp.add_argument("--quiet", type=float, default=None, help="이벤트 묶음 대기(초)")
    wp.add_argument("--stable", type=float, default=None, help="파일 안정화 대기(초)")
    wp.add_argument("--json", action="store_true")
    wp.set_defaults(func=cmd_watch)

    cp = sub.add_parser("zipcheck", help="zip이 윈도우에서 안전한지 검사")
    cp.add_argument("zips", nargs="+")
    cp.add_argument("--json", action="store_true")
    cp.set_defaults(func=cmd_zipcheck)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n중단됨", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
