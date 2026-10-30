#!/bin/bash
# 배포용 DMG 생성 — build/HanFix.app 이 있어야 한다.
set -euo pipefail
cd "$(dirname "$0")/.."

APP="build/HanFix.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
DMG="build/HanFix-${VERSION}.dmg"
STAGE="build/dmg-stage"

[ -d "$APP" ] || { echo "먼저 scripts/build.sh 를 실행하세요"; exit 1; }

echo "── DMG 스테이징"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/HanFix.app"
ln -s /Applications "$STAGE/Applications"

cat > "$STAGE/설치 안내.txt" <<'TXT'
HanFix 설치 방법

1. HanFix.app 을 오른쪽 Applications 폴더로 끌어다 놓으세요.
2. 응용 프로그램 폴더에서 HanFix 를 실행하세요.
3. 처음 실행할 때 "확인되지 않은 개발자입니다" 창이 뜨면:
   HanFix 를 우클릭 → 열기 → 열기 를 누르면 실행됩니다. (한 번만 하면 됩니다)

자동 감시를 켜려면: 앱 상단의 "설정" → "로그인할 때 자동 시작" 을 켜고 저장하세요.

자세한 사용법은 GitHub 저장소의 docs/GUIDE.ko.md 를 참고하세요.
TXT

echo "── 압축 (UDZO)"
hdiutil create -volname "HanFix $VERSION" \
  -srcfolder "$STAGE" -ov -format UDZO \
  -fs HFS+ "$DMG" >/dev/null

rm -rf "$STAGE"
SIZE=$(du -h "$DMG" | cut -f1)
echo "── 완료: $DMG ($SIZE)"
shasum -a 256 "$DMG" | tee "build/HanFix-${VERSION}.dmg.sha256"
