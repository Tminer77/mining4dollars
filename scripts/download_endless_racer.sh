#!/usr/bin/env bash
# Download Endless Racer (UE4 Endless Racer by Tomiinek, MIT).
#
# Fetches:
#   1. Source project (Endless.uproject, UE 4.21) for Unreal Editor
#   2. Optional Windows build from the author's Google Drive mirror (may 404)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${RACING_DOWNLOAD_DIR:-$ROOT/third_party/racing}"
SOURCE_DIR="$DEST/UE4_Endless_Racer"
BUILD_DIR="$DEST/EndlessRacer-Windows"
GDRIVE_ID="1vaaN-xmJyTJwTgppxts9I0V6VZd1gTJ6"
GDRIVE_URL="https://drive.google.com/uc?id=${GDRIVE_ID}"

mkdir -p "$DEST"

clone_source() {
  if [ -d "$SOURCE_DIR/.git" ]; then
    echo "Endless Racer source already present at $SOURCE_DIR"
    return
  fi
  echo "Cloning UE4_Endless_Racer (MIT, Unreal Engine 4.21 Blueprints)…"
  git clone --depth 1 https://github.com/Tomiinek/UE4_Endless_Racer.git "$SOURCE_DIR"
  echo "Source ready: $SOURCE_DIR/Endless.uproject"
  echo "Open in Unreal Engine 4.21+ (Editor → File → Open Project)."
}

download_windows_build() {
  mkdir -p "$BUILD_DIR"
  local archive="$BUILD_DIR/EndlessRacer-Windows.zip"
  if find "$BUILD_DIR" -type f -name '*.exe' 2>/dev/null | grep -q .; then
    echo "Windows build already present under $BUILD_DIR"
    return 0
  fi
  echo "Downloading Windows build from author Google Drive mirror…"
  set +e
  if command -v gdown >/dev/null 2>&1; then
    gdown "$GDRIVE_URL" -O "$archive"
  else
    python3 -m pip install --user gdown -q
    python3 -m gdown "$GDRIVE_URL" -O "$archive"
  fi
  local rc=$?
  set -e
  if [ "$rc" -ne 0 ] || [ ! -f "$archive" ] || ! file "$archive" | grep -qi 'zip\|archive'; then
    rm -f "$archive"
    echo "WARNING: Windows build is unavailable (Drive link private/expired: $GDRIVE_ID)." >&2
    echo "Source project is enough for Unreal Editor. Skipping binary." >&2
    return 0
  fi
  echo "Extracting Windows build…"
  unzip -qo "$archive" -d "$BUILD_DIR"
  echo "Windows build ready under $BUILD_DIR"
}

usage() {
  cat <<'EOF'
Usage: scripts/download_endless_racer.sh [source|build|all]

  source   Clone UE4_Endless_Racer source (default)
  build    Download and extract the Windows shipping build
  all      Source + Windows build
EOF
}

target="${1:-all}"
case "$target" in
  -h|--help|help)
    usage
    ;;
  source|src)
    clone_source
    ;;
  build|win|windows)
    download_windows_build
    ;;
  all)
    clone_source
    download_windows_build
    ;;
  *)
    echo "Unknown target: $target" >&2
    usage >&2
    exit 1
    ;;
esac
