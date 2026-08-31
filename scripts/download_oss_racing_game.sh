#!/usr/bin/env bash
# Download legitimate open-source racing games.
#
# There is no legal source for Grand Theft Auto 6 (or any Rockstar) graphics.
# Those assets are proprietary. This script only fetches games that publish
# their own code and art under OSI-approved licenses.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${RACING_DOWNLOAD_DIR:-$ROOT/third_party/racing}"
mkdir -p "$DEST"

usage() {
  cat <<'EOF'
Usage: scripts/download_oss_racing_game.sh [hexgl|stuntrally|all]

  hexgl       HexGL (MIT, HTML5, ~15 MB) — default
  stuntrally  Stunt Rally 3 source (GPL-3.0, ~1.5 GB git checkout)
  all         Both of the above

Games land in third_party/racing/ and are gitignored.
Stunt Rally 3 is the most realistic *complete* open-source racer.
Nothing here is GTA 6, and nothing here uses Rockstar assets.
EOF
}

clone_hexgl() {
  local dir="$DEST/HexGL"
  if [ -d "$dir/.git" ]; then
    echo "HexGL already present at $dir"
    return
  fi
  echo "Cloning HexGL (MIT, HTML5 racing)…"
  git clone --depth 1 https://github.com/BKcore/HexGL.git "$dir"
  echo "HexGL ready: $dir"
  echo "Play: python -m http.server 8081 --directory $dir"
}

clone_stuntrally() {
  local dir="$DEST/stuntrally3"
  if [ -d "$dir/.git" ]; then
    echo "Stunt Rally 3 already present at $dir"
    return
  fi
  echo "Cloning Stunt Rally 3 (GPL-3.0, Ogre-Next, ~1.5 GB)…"
  echo "This is source plus data. A Linux binary release is ~2 GB on SourceForge."
  git clone --depth 1 https://github.com/stuntrally/stuntrally3.git "$dir"
  echo "Stunt Rally 3 ready: $dir"
  echo "Build docs: $dir/docs/Install.md"
}

target="${1:-hexgl}"
case "$target" in
  -h|--help|help)
    usage
    ;;
  hexgl)
    clone_hexgl
    ;;
  stuntrally|stuntrally3)
    clone_stuntrally
    ;;
  all)
    clone_hexgl
    clone_stuntrally
    ;;
  *)
    echo "Unknown target: $target" >&2
    usage >&2
    exit 1
    ;;
esac
