#!/usr/bin/env python3
"""감시 데몬 통합 테스트 — 실제 FSEvents 를 쓴다.

고정 sleep 대신 '준비됨' 로그와 파일 상태를 기다린다(러너 속도 차이에 강하다).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata as u
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "bin", "hanfix")
NFC = lambda s: u.normalize("NFC", s)   # noqa: E731
NFD = lambda s: u.normalize("NFD", s)   # noqa: E731


def wait_for(pred, timeout=10.0, interval=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except OSError:
            pass
        time.sleep(interval)
    return False


def read_text(path, limit=4000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-limit:]
    except OSError:
        return ""


class WatchTest(unittest.TestCase):
    def setUp(self):
        base = os.path.expanduser("~/tmp")
        self.tmp = tempfile.mkdtemp(prefix="hanfix-watch-",
                                    dir=base if os.path.isdir(base) else None)
        # 로그는 감시 폴더 '밖'에 둔다(목록 검사에 끼어들지 않도록)
        self.logfile = self.tmp.rstrip("/") + ".log"
        self.env = dict(os.environ, PYTHONPATH="",
                        HANFIX_WATCH_LOG=self.logfile,
                        PYTHONUTF8="1")
        self.proc = subprocess.Popen(
            [CLI, "watch", self.tmp, "--fg", "--quiet", "0.4", "--stable", "0.4"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=self.env)
        if not wait_for(lambda: "준비됨" in read_text(self.logfile), timeout=20):
            out = ""
            try:
                self.proc.kill()
                out = self.proc.communicate(timeout=5)[0]
            except Exception:
                pass
            self.fail("감시자가 시작되지 않았습니다.\n로그:\n"
                      + read_text(self.logfile) + "\n출력:\n" + (out or ""))

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)
        if os.path.exists(self.logfile):
            os.remove(self.logfile)

    def names(self, d=None):
        return os.listdir(d or self.tmp)

    def wait_name(self, folder, want, timeout=12):
        return wait_for(lambda: want in self.names(folder), timeout)

    def test_new_nfd_file_is_renamed(self):
        open(os.path.join(self.tmp, NFD("다운로드_보고서") + ".hwp"), "w").write("x")
        self.assertTrue(self.wait_name(self.tmp, NFC("다운로드_보고서") + ".hwp"),
                        "자동 정리 실패. 로그:\n" + read_text(self.logfile))
        self.assertFalse(any(not u.normalize("NFC", n) == n for n in self.names()
                             if n.endswith(".hwp")))

    def test_content_untouched(self):
        open(os.path.join(self.tmp, NFD("내용보존") + ".txt"), "w").write("안녕하세요 내용")
        q = os.path.join(self.tmp, NFC("내용보존") + ".txt")
        self.assertTrue(self.wait_name(self.tmp, NFC("내용보존") + ".txt"),
                        read_text(self.logfile))
        with open(q) as f:
            self.assertEqual(f.read(), "안녕하세요 내용")

    def test_partial_download_untouched(self):
        open(os.path.join(self.tmp, NFD("받는중") + ".crdownload"), "w").write("x")
        time.sleep(3)
        self.assertIn(NFD("받는중") + ".crdownload", self.names(),
                      "다운로드 중 파일을 건드리면 브라우저가 실패한다")

    def test_git_project_untouched(self):
        repo = os.path.join(self.tmp, "myrepo")
        os.makedirs(os.path.join(repo, ".git"))
        open(os.path.join(repo, "tracked.txt"), "w").write("x")
        open(os.path.join(repo, NFD("소스파일") + ".py"), "w").write("x")
        time.sleep(3)
        self.assertIn(NFD("소스파일") + ".py", os.listdir(repo),
                      "git 저장소 안쪽 이름을 바꾸면 작업트리가 깨진다")

    def test_nested_dir_and_child(self):
        d = os.path.join(self.tmp, NFD("새폴더"))
        os.makedirs(d)
        open(os.path.join(d, NFD("안쪽파일") + ".pdf"), "w").write("x")
        self.assertTrue(self.wait_name(self.tmp, NFC("새폴더"), 15),
                        read_text(self.logfile))
        inner = os.path.join(self.tmp, NFC("새폴더"))
        self.assertTrue(self.wait_name(inner, NFC("안쪽파일") + ".pdf"),
                        read_text(self.logfile))

    def test_undo_reverts_watch_action(self):
        open(os.path.join(self.tmp, NFD("되돌릴파일") + ".txt"), "w").write("x")
        self.assertTrue(self.wait_name(self.tmp, NFC("되돌릴파일") + ".txt"),
                        read_text(self.logfile))
        out = subprocess.run([CLI, "undo", "--json"], capture_output=True,
                             text=True, encoding="utf-8", env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(self.wait_name(self.tmp, NFD("되돌릴파일") + ".txt", 5),
                        read_text(self.logfile))


if __name__ == "__main__":
    unittest.main(verbosity=2)
