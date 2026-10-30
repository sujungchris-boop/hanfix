#!/bin/bash
# 내장 파이썬 런타임 내려받기 (릴리스용). vendor/ 는 git 에 커밋하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY_VER="3.12.16"
PBS_TAG="20261026"
DEST="vendor"

mkdir -p "$DEST"
cd "$DEST"

for arch in aarch64 x86_64; do
  target="python-$arch"
  if [ -x "$target/bin/python3" ]; then
    echo "── 이미 있음: $target"
    continue
  fi
  file="cpython-${PY_VER}+${PBS_TAG}-${arch}-apple-darwin-install_only.tar.gz"
  url="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${file}"
  echo "── 내려받기 ${arch}"
  curl -fsSL --retry 3 -o "$file" "$url"
  tar xzf "$file"
  mv python "$target"
  rm -f "$file"

  # 실행에 불필요한 것 제거 (테스트·IDLE·tkinter·pip 등) — 약 20% 감소
  rm -rf "$target"/lib/python3.12/{test,idlelib,turtledemo,ensurepip,lib2to3,pydoc_data,tkinter,unittest,site-packages} \
         "$target"/include "$target"/share 2>/dev/null || true
  rm -f "$target"/lib/python3.12/lib-dynload/_tkinter*.so \
        "$target"/bin/{pip3*,idle3*,2to3*,pydoc3*} 2>/dev/null || true
  strip -x "$target"/bin/python3.12 2>/dev/null || true
done

echo "── 완료"
du -sh python-*
