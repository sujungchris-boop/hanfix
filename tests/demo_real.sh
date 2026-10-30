#!/bin/bash
# 실제 코퍼스 파일로 end-to-end 시연
set -e
H=/Applications/HanFix.app/Contents/Resources/hanfix/bin/hanfix
W=~/tmp/real-world
rm -rf "$W"; mkdir -p "$W/제출본"

# 실제 NFD 파일 3개를 복사 (이름 그대로 유지됨)
SRC="/Volumes/Dev/제안서 학습용"
i=0
find "$SRC" -maxdepth 2 -name "*.pdf" -o -maxdepth 2 -name "*.hwpx" 2>/dev/null | head -3 | while read -r f; do
  cp "$f" "$W/제출본/" 2>/dev/null || true
done
cp "$SRC/(정성제안서)2025년 국립농업박물관 문화행사_운영 용역 대행 제안서.pdf" "$W/제출본/" 2>/dev/null || true

echo "──── ① 지금 상태 (디스크 원바이트) ────"
PYTHONPATH= python3 - "$W/제출본" <<'PY'
import os, sys, unicodedata as u
d = sys.argv[1]
for n in sorted(os.listdir(d)):
    print(f"  {'NFD':4s}" if u.normalize('NFC', n) != n else "  NFC ", end="")
    print(n.encode('unicode_escape').decode()[:78])
PY

echo
echo "──── ② 스캔 ────"
$H scan "$W" | head -8

echo
echo "──── ③ 정리 적용 ────"
$H fix "$W" --apply | head -6

echo
echo "──── ④ 정리 후 (디스크 원바이트) ────"
PYTHONPATH= python3 - "$W/제출본" <<'PY'
import os, sys, unicodedata as u
d = sys.argv[1]
bad = 0
for n in sorted(os.listdir(d)):
    if u.normalize('NFC', n) != n:
        bad += 1
    print(f"  {'NFD':4s}" if u.normalize('NFC', n) != n else "  NFC ", end="")
    print(n[:70])
print(f"  → NFD 잔존 {bad}개")
PY

echo
echo "──── ⑤ 윈도우용 압축 ────"
cd "$W" && $H zip 제출본 -o "$W/제출본_윈도우용.zip"
echo
echo "──── ⑥ 압축 검사 (맥 기본 압축과 비교) ────"
$H zipcheck "$W/제출본_윈도우용.zip"
rm -f "$W/맥기본.zip"
ditto -c -k --norsrc "$W/제출본" "$W/맥기본.zip"
$H zipcheck "$W/맥기본.zip" || true
