#!/usr/bin/env python3
"""hanfix 자체 검증 — 실제 APFS 볼륨에서 실측한다."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata as u
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hanfix import core  # noqa: E402

NFC = lambda s: u.normalize("NFC", s)   # noqa: E731
NFD = lambda s: u.normalize("NFD", s)   # noqa: E731
UTF8_FLAG = 0x800


def build_fixture(base):
    """NFD 이름이 섞인 현실적인 제안서 폴더."""
    tree = {
        NFD("발표자료"): {
            NFD("행사_제안서.hwp"): "hwp-body",
            NFD("참가자 명단.xlsx"): "xlsx-body",
            NFD("사진"): {
                NFD("단체사진.jpg"): "jpg-body",
                "normal.txt": "ascii",
            },
        },
        NFD("견적서.pdf"): "pdf-body",
        "readme.txt": "plain",
    }
    for name, content in tree.items():
        p = os.path.join(base, name)
        if isinstance(content, str):
            with open(p, "w") as f:
                f.write(content)
            continue
        os.makedirs(p, exist_ok=True)
        for cname, body in content.items():
            cp = os.path.join(p, cname)
            if isinstance(body, dict):
                os.makedirs(cp, exist_ok=True)
                for n2, b2 in body.items():
                    with open(os.path.join(cp, n2), "w") as f:
                        f.write(b2)
            else:
                with open(cp, "w") as f:
                    f.write(body)
    # 패키지(.app) 내부는 건드리면 안 된다
    app = os.path.join(base, NFD("편집기.app"))
    os.makedirs(os.path.join(app, "Contents", "MacOS"), exist_ok=True)
    with open(os.path.join(app, "Contents", "MacOS", NFD("실행파일")), "w") as f:
        f.write("bin")
    with open(os.path.join(base, ".DS_Store"), "w") as f:
        f.write("junk")
    # 심볼릭 링크: 링크 자신의 이름만 정리, 대상은 그대로
    os.symlink(os.path.join(base, NFD("견적서.pdf")), os.path.join(base, NFD("바로가기")))


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hanfix-test-", dir=os.path.expanduser("~/tmp")) \
            if os.path.isdir(os.path.expanduser("~/tmp")) else tempfile.mkdtemp(prefix="hanfix-test-")
        self.base = os.path.join(self.tmp, NFD("제안서_최종"))
        os.makedirs(self.base)
        build_fixture(self.base)
        core.JOURNAL_PATH = os.path.join(self.tmp, "journal.jsonl")
        core.JOURNAL_DIR = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, path):
        return os.listdir(path)

    # ── 1. 스캔
    def test_scan_finds_nfd_and_skips_package_inside(self):
        res = core.scan([self.base])
        nfd_rel = {e.rel for e in res["entries"] if e.changes}
        base_names = {os.path.basename(r) for r in nfd_rel}
        self.assertIn(NFD("행사_제안서.hwp"), base_names)
        self.assertIn(NFD("사진"), base_names)          # NFD 디렉터리
        # .app 은 이름만 대상, 내부는 스캔하지 않는다
        self.assertIn(NFD("편집기.app"), base_names)
        self.assertFalse(any(NFD("실행파일") == os.path.basename(r) for r in nfd_rel))
        self.assertFalse(any("/Contents" in r or r.endswith("Contents") for r in nfd_rel))
        self.assertGreaterEqual(res["counts"]["nfd"], 6)
        self.assertEqual(res["counts"]["skip"], 0)

    def test_volume_detected_as_nfc_capable(self):
        res = core.scan([self.base])
        self.assertEqual(res["volume"]["fstype"], "apfs")
        self.assertTrue(res["volume"]["nfc_capable"])

    # ── 2. 실제 변경
    def test_fix_makes_everything_nfc_and_keeps_content(self):
        res = core.scan([self.base])
        out = core.apply_fix(res["entries"], dry_run=False)
        self.assertEqual(out["failed"], [])
        self.assertGreater(out["renamed"], 0)

        for dirpath, dirnames, filenames in os.walk(self.base):
            if ".app" in dirpath:
                continue          # 패키지 내부는 설계상 손대지 않는다
            for n in dirnames + filenames:
                self.assertTrue(core.is_nfc(n), f"NFD 잔존: {n!r}")

        # 파일 내용 보존
        with open(os.path.join(self.base, NFC("발표자료"), NFC("행사_제안서.hwp"))) as f:
            self.assertEqual(f.read(), "hwp-body")
        # .app 내부는 손대지 않음
        app = os.path.join(self.base, NFC("편집기.app"), "Contents", "MacOS")
        self.assertEqual(os.listdir(app), [NFD("실행파일")])

    def test_fix_is_idempotent(self):
        core.apply_fix(core.scan([self.base])["entries"], dry_run=False)
        again = core.scan([self.base])
        self.assertEqual(again["counts"]["nfd"], 0)
        out = core.apply_fix(again["entries"], dry_run=False)
        self.assertEqual(out["renamed"], 0)

    def test_inode_preserved(self):
        before = {}
        for dp, dn, fn in os.walk(self.base):
            for n in fn:
                p = os.path.join(dp, n)
                if not os.path.islink(p):
                    before[os.stat(p).st_ino] = os.path.relpath(p, self.base)
        core.apply_fix(core.scan([self.base])["entries"], dry_run=False)
        after = {}
        for dp, dn, fn in os.walk(self.base):
            for n in fn:
                p = os.path.join(dp, n)
                if not os.path.islink(p):
                    after[os.stat(p).st_ino] = os.path.relpath(p, self.base)
        self.assertEqual(set(before), set(after))

    def test_dry_run_changes_nothing(self):
        before = sorted(os.listdir(self.base))
        core.apply_fix(core.scan([self.base])["entries"], dry_run=True)
        self.assertEqual(sorted(os.listdir(self.base)), before)

    def test_undo_restores_exactly(self):
        before = sorted(os.listdir(self.base))
        core.apply_fix(core.scan([self.base])["entries"], dry_run=False)
        self.assertNotEqual(sorted(os.listdir(self.base)), before)
        res = core.undo()
        self.assertEqual(res["failed"], [])
        self.assertGreater(res["restored"], 0)
        self.assertEqual(sorted(os.listdir(self.base)), before)

    # ── 3. zip
    def test_zip_is_windows_safe(self):
        out = os.path.join(self.tmp, "result.zip")
        res = core.create_zip([self.base], out)
        self.assertEqual(res["verify"]["non_nfc"], 0)
        self.assertEqual(res["verify"]["missing_utf8_flag"], 0)
        with zipfile.ZipFile(res["out"]) as z:
            for i in z.infolist():
                self.assertTrue(core.is_nfc(i.filename), i.filename)
                if any(ord(c) > 127 for c in i.filename):
                    self.assertTrue(i.flag_bits & UTF8_FLAG, i.filename)
            self.assertFalse(any(".DS_Store" in i.filename for i in z.infolist()))

    def test_macos_zip_is_unsafe_our_zip_is_safe(self):
        """맥 기본 압축이 실제로 위험하다는 것을 대조 증명."""
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            subprocess.run(["/usr/bin/zip", "-qr", "native.zip",
                            os.path.basename(self.base)], check=True)
            bad = core.check_zip(os.path.join(self.tmp, "native.zip"))
            self.assertFalse(bad["safe"])
            self.assertGreater(bad["non_nfc_count"], 0)
            self.assertGreater(bad["missing_utf8_flag_count"], 0)

            good = core.create_zip([self.base], os.path.join(self.tmp, "good.zip"))
            self.assertTrue(core.check_zip(good["out"])["safe"])
        finally:
            os.chdir(cwd)

    # ── 4. unzip
    def test_unzip_recovers_cp949_names(self):
        """옛 윈도우 도구가 만든 CP949 이름 zip → 한글 복원 + NFC."""
        class RawNameInfo(zipfile.ZipInfo):
            """플래그 없이 CP949 원바이트로 이름을 심는 ZipInfo."""
            def __init__(self, raw_bytes, flag_bits=0):
                super().__init__(raw_bytes.decode("cp437"))
                self._raw = raw_bytes
                self.flag_bits = flag_bits

            def _encodeFilenameFlags(self):
                return self._raw, self.flag_bits

        src = os.path.join(self.tmp, "win.zip")
        with zipfile.ZipFile(src, "w") as z:
            for name, body in [("보고서.hwp", "a"), ("사진/단체.jpg", "b")]:
                z.writestr(RawNameInfo(name.encode("cp949")), body)
        with zipfile.ZipFile(src) as z:
            infos = z.infolist()
            self.assertTrue(all(not (i.flag_bits & UTF8_FLAG) for i in infos),
                            "테스트 픽스처는 플래그가 없어야 한다")
        dest = os.path.join(self.tmp, "out")
        res = core.extract_zip(src, dest=dest)
        self.assertEqual(res["errors"], [])
        got = sorted(os.path.relpath(os.path.join(dp, n), dest)
                     for dp, dn, fn in os.walk(dest) for n in dn + fn)
        self.assertIn("보고서.hwp", got)
        self.assertIn("사진", got)
        self.assertEqual(res["non_nfc_left"], 0)

    def test_unzip_normalizes_nfd_entries(self):
        src = os.path.join(self.tmp, "mac.zip")
        with zipfile.ZipFile(src, "w") as z:
            z.writestr(NFD("발표자료") + "/" + NFD("개요.hwp"), "x")
        dest = os.path.join(self.tmp, "out2")
        res = core.extract_zip(src, dest=dest)
        self.assertGreater(res["names_repaired"], 0)
        self.assertEqual(res["non_nfc_left"], 0)
        self.assertTrue(os.path.exists(os.path.join(dest, NFC("발표자료"), NFC("개요.hwp"))))

    def test_unzip_blocks_path_traversal(self):
        src = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("../../etc/passwd", "x")
        res = core.extract_zip(src, dest=os.path.join(self.tmp, "out3"))
        self.assertTrue(res["skipped"] or res["files"] == 0)
        self.assertFalse(os.path.exists("/etc/passwd.hanfix"))

    # ── 5. CLI
    def test_cli_json_roundtrip(self):
        cli = os.path.join(ROOT, "bin", "hanfix")
        env = dict(os.environ, PYTHONPATH="")
        out = subprocess.run([cli, "scan", self.base, "--json"],
                             capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(out.returncode, 1, out.stderr)
        data = json.loads(out.stdout)
        self.assertGreater(data["counts"]["nfd"], 0)
        self.assertEqual(data["volume"]["fstype"], "apfs")

        out2 = subprocess.run([cli, "scan", self.base, "--json", "--quiet"],
                              capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(json.loads(out2.stdout)["counts"]["nfd"],
                         data["counts"]["nfd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
