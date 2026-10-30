#!/usr/bin/env python3
"""감시 데몬 통합 테스트 — 실제 FSEvents 를 쓴다."""
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


class WatchTest(unittest.TestCase):
    def setUp(self):
        base = "~/tmp" if os.path.isdir(os.path.expanduser("~/tmp")) else None
        self.tmp = tempfile.mkdtemp(prefix="hanfix-watch-",
                                    dir=os.path.expanduser(base) if base else None)
        self.env = dict(os.environ, PYTHONPATH="",
                        HANFIX_WATCH_LOG=os.path.join(self.tmp, "test.log"))
        self.proc = subprocess.Popen(
            [CLI, "watch", self.tmp, "--fg", "--quiet", "0.4", "--stable", "0.4"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.env)
        time.sleep(1.5)

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, d=None):
        return os.listdir(d or self.tmp)

    def test_new_nfd_file_is_renamed(self):
        p = os.path.join(self.tmp, NFD("다운로드_보고서") + ".hwp")
        open(p, "w").write("x")
        time.sleep(4)
        self.assertIn(NFC("다운로드_보고서") + ".hwp", self.names())
        self.assertFalse(any(not u.normalize("NFC", n) == n for n in self.names()))

    def test_content_untouched(self):
        p = os.path.join(self.tmp, NFD("내용보존") + ".txt")
        open(p, "w").write("안녕하세요 내용")
        time.sleep(4)
        q = os.path.join(self.tmp, NFC("내용보존") + ".txt")
        self.assertTrue(os.path.exists(q))
        self.assertEqual(open(q).read(), "안녕하세요 내용")

    def test_partial_download_untouched(self):
        p = os.path.join(self.tmp, NFD("받는중") + ".crdownload")
        open(p, "w").write("x")
        time.sleep(4)
        self.assertIn(NFD("받는중") + ".crdownload", self.names(),
                      "다운로드 중 파일을 건드리면 브라우저가 실패한다")

    def test_git_project_untouched(self):
        repo = os.path.join(self.tmp, "myrepo")
        os.makedirs(os.path.join(repo, ".git"))
        open(os.path.join(repo, "tracked.txt"), "w").write("x")
        p = os.path.join(repo, NFD("소스파일") + ".py")
        open(p, "w").write("x")
        time.sleep(4)
        self.assertIn(NFD("소스파일") + ".py", os.listdir(repo),
                      "git 저장소 안쪽 이름을 바꾸면 작업트리가 깨진다")

    def test_nested_dir_and_child(self):
        d = os.path.join(self.tmp, NFD("새폴더"))
        os.makedirs(d)
        open(os.path.join(d, NFD("안쪽파일") + ".pdf"), "w").write("x")
        time.sleep(5)
        top = self.names()
        self.assertIn(NFC("새폴더"), top)
        inner = os.listdir(os.path.join(self.tmp, NFC("새폴더")))
        self.assertIn(NFC("안쪽파일") + ".pdf", inner)

    def test_undo_reverts_watch_action(self):
        p = os.path.join(self.tmp, NFD("되돌릴파일") + ".txt")
        open(p, "w").write("x")
        time.sleep(4)
        self.assertIn(NFC("되돌릴파일") + ".txt", self.names())
        out = subprocess.run([CLI, "undo", "--json"], capture_output=True,
                             text=True, env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(NFD("되돌릴파일") + ".txt", self.names())


if __name__ == "__main__":
    unittest.main(verbosity=2)
