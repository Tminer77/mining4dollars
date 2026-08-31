#!/usr/bin/env bash
# Launch Endless Racer: Unreal Editor if installed, else Wine Windows build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${RACING_DOWNLOAD_DIR:-$ROOT/third_party/racing}"
SOURCE_PROJECT="$DEST/UE4_Endless_Racer/Endless.uproject"
BUILD_ROOT="$DEST/EndlessRacer-Windows"

find_exe() {
  find "$BUILD_ROOT" -type f \( -name 'Endless*.exe' -o -name '*Shipping.exe' \) 2>/dev/null | head -1
}

find_editor() {
  local candidates=(
    "${UNREAL_EDITOR:-}"
    "${UE4_ROOT:-}/Engine/Binaries/Linux/UE4Editor"
    "${UE4_ROOT:-}/Engine/Binaries/Linux/UnrealEditor"
    "${UE5_ROOT:-}/Engine/Binaries/Linux/UnrealEditor"
    "$HOME/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"
    "$HOME/UnrealEngine/Engine/Binaries/Linux/UE4Editor"
    "$HOME/ue4/Engine/Binaries/Linux/UE4Editor"
    "/opt/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"
    "/opt/UnrealEngine/Engine/Binaries/Linux/UE4Editor"
  )
  local c
  for c in "${candidates[@]}"; do
    if [ -n "$c" ] && [ -x "$c" ]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

ensure_source() {
  if [ ! -f "$SOURCE_PROJECT" ]; then
    echo "Endless Racer source missing. Cloning…"
    bash "$ROOT/scripts/download_endless_racer.sh" source
  fi
}

ensure_source

if editor="$(find_editor)"; then
  echo "Opening Endless Racer in Unreal Editor:"
  echo "  $editor"
  echo "  $SOURCE_PROJECT"
  exec env DISPLAY="${DISPLAY:-:1}" "$editor" "$SOURCE_PROJECT"
fi

exe="$(find_exe || true)"
if [ -z "$exe" ]; then
  bash "$ROOT/scripts/download_endless_racer.sh" build || true
  exe="$(find_exe || true)"
fi

if [ -n "$exe" ] && command -v wine >/dev/null 2>&1; then
  echo "Unreal Editor not found. Starting Windows build via Wine: $exe"
  cd "$(dirname "$exe")"
  exec env DISPLAY="${DISPLAY:-:1}" WINEDEBUG=-all wine "$exe"
fi

cat <<EOF >&2
Endless Racer source is ready:
  $SOURCE_PROJECT

Cannot start it here because:
  - Unreal Engine / UE4Editor is not installed on this machine
    (Epic source requires a linked Epic Games GitHub account)
  - The author's Windows build Google Drive mirror is unavailable

On a machine with Unreal Engine 4.21+ installed:
  1. make download-endless
  2. Open $SOURCE_PROJECT in the editor
  3. Play (Alt+P) on Level.umap

Or set UNREAL_EDITOR=/path/to/UE4Editor and re-run: make endless-racer
EOF
exit 1
