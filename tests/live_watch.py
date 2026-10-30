import os, subprocess, time, unicodedata as u

H = "/Applications/HanFix.app/Contents/Resources/hanfix/bin/hanfix"
NFC = lambda s: u.normalize("NFC", s)
NFD = lambda s: u.normalize("NFD", s)
HOME = os.path.expanduser("~")
results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(("PASS " if ok else "FAIL ") + label + (f"  {detail}" if detail else ""), flush=True)


def names(d):
    return os.listdir(os.path.join(HOME, d))


def wait_rename(folder, nfd_name, nfc_name, timeout=12):
    end = time.time() + timeout
    while time.time() < end:
        if nfc_name in names(folder) and nfd_name not in names(folder):
            return True
        time.sleep(0.5)
    return nfc_name in names(folder)


print("=== 1) 다운로드 / 문서 / 데스크탑 각각 ===", flush=True)
for folder, base in [("Downloads", "폴더테스트_다운"), ("Documents", "폴더테스트_문서"), ("Desktop", "폴더테스트_데스크")]:
    p = os.path.join(HOME, folder, NFD(base) + ".txt")
    open(p, "w").write("x")
    ok = wait_rename(folder, NFD(base) + ".txt", NFC(base) + ".txt")
    check(f"{folder} 자동 정리", ok)

print("\n=== 2) 다운로드 중 파일(.crdownload)은 건드리지 않아야 ===", flush=True)
p = os.path.join(HOME, "Downloads", NFD("받는중") + ".crdownload")
open(p, "w").write("partial")
time.sleep(6)
check(".crdownload 보존", NFD("받는중.crdownload") in names("Downloads"))
os.remove(p)

print("\n=== 3) 쓰는 중인 파일은 완료 후에 정리 ===", flush=True)
p = os.path.join(HOME, "Downloads", NFD("쓰는중") + ".txt")
with open(p, "w") as f:
    for i in range(6):
        f.write(f"line{i}\n")
        f.flush()
        time.sleep(0.7)
    still_nfd = NFD("쓰는중.txt") in names("Downloads")
check("작성 중에는 이름 유지", still_nfd)
ok = wait_rename("Downloads", NFD("쓰는중") + ".txt", NFC("쓰는중") + ".txt", timeout=15)
check("작성 완료 후 정리", ok)

print("\n=== 4) git 저장소 안쪽은 보호 ===", flush=True)
repo = os.path.join(HOME, "Downloads", "_hanfix_git_test")
os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
open(os.path.join(repo, NFD("소스") + ".py"), "w").write("x")
time.sleep(6)
check("git 저장소 내부 보존", NFD("소스.py") in os.listdir(repo))

print("\n=== 5) 내용 보존 ===", flush=True)
p = os.path.join(HOME, "Downloads", NFD("내용검사") + ".txt")
open(p, "w").write("제안서 본문 내용 12345")
ok = wait_rename("Downloads", NFD("내용검사") + ".txt", NFC("내용검사") + ".txt")
body = open(os.path.join(HOME, "Downloads", NFC("내용검사") + ".txt")).read() if ok else ""
check("내용 그대로", body == "제안서 본문 내용 12345", repr(body))

print("\n=== 6) 되돌리기 (저널) ===", flush=True)
out = subprocess.run([H, "undo", "--json"], capture_output=True, text=True)
try:
    import json
    j = json.loads(out.stdout)
    check("undo 동작", j.get("restored", 0) > 0, f"restored={j.get('restored')} failed={len(j.get('failed', []))}")
except Exception as e:
    check("undo 동작", False, str(e))

print("\n=== 정리 ===", flush=True)
for folder in ("Downloads", "Documents", "Desktop"):
    for n in names(folder):
        if any(k in NFC(n) for k in ("폴더테스트", "내용검사", "쓰는중", "자동정리_테스트", "권한테스트", "앱경유_테스트", "재기동테스트")):
            try:
                os.remove(os.path.join(HOME, folder, n))
                print(f"  removed {folder}/{n}", flush=True)
            except OSError as e:
                print(f"  skip {n}: {e}", flush=True)
import shutil
shutil.rmtree(os.path.join(HOME, "Downloads", "_hanfix_git_test"), ignore_errors=True)
print("  removed _hanfix_git_test", flush=True)

print("\n=== 요약 ===")
for label, ok, _ in results:
    print(("PASS " if ok else "FAIL ") + label)
print(f"{sum(1 for _, ok, _ in results if ok)}/{len(results)} 통과")
