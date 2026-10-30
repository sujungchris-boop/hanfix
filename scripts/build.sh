#!/bin/bash
# HanFix.app 빌드 — 유니버설(arm64 + x86_64), 파이썬 런타임 내장, 의존성 없음
set -euo pipefail
cd "$(dirname "$0")/.."

APP="build/HanFix.app"
VERSION="1.1.0"
BUILD_NUM="$(date +%Y%m%d)"
SOURCES=(app/HanFixApp.swift app/SettingsView.swift)

echo "── 1/6 정리"
rm -rf "$APP" build/HanFix-arm64 build/HanFix-x86_64
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/hanfix" "$APP/Contents/Resources/runtime"

echo "── 2/6 컴파일 (universal)"
for arch in arm64 x86_64; do
  swiftc -O -swift-version 5 -parse-as-library \
    -target "${arch}-apple-macosx13.0" \
    -framework SwiftUI -framework AppKit \
    -o "build/HanFix-${arch}" "${SOURCES[@]}"
done
lipo -create -output "$APP/Contents/MacOS/HanFix" build/HanFix-arm64 build/HanFix-x86_64
lipo -info "$APP/Contents/MacOS/HanFix"

echo "── 3/6 리소스"
cp app/Info.plist "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUM" "$APP/Contents/Info.plist" 2>/dev/null || true
[ -f app/AppIcon.icns ] && cp app/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

cp -R hanfix "$APP/Contents/Resources/hanfix/"
cp -R bin "$APP/Contents/Resources/hanfix/"
find "$APP/Contents/Resources/hanfix" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "── 4/6 파이썬 런타임 내장"
if [ -d vendor/python-aarch64 ]; then
  rsync -a --exclude '__pycache__' vendor/python-aarch64/ "$APP/Contents/Resources/runtime/arm64/"
fi
if [ -d vendor/python-x86_64 ]; then
  rsync -a --exclude '__pycache__' vendor/python-x86_64/ "$APP/Contents/Resources/runtime/x86_64/"
fi
# 런타임이 없으면 시스템 파이썬으로 폴백한다(개발 빌드)

echo "── 5/6 서명 (애드혹)"
# 중첩 실행 파일을 먼저 서명해야 --deep 서명이 깨지지 않는다
find "$APP/Contents/Resources/runtime" -type f -perm +111 2>/dev/null | while read -r f; do
  case "$(file -b "$f")" in
    Mach-O*) codesign --force -s - "$f" 2>/dev/null || true ;;
  esac
done
codesign --force --deep -s - "$APP" 2>/dev/null || echo "  (서명 생략)"

echo "── 6/6 완료: $APP ($(du -sh "$APP" | cut -f1), v$VERSION)"
