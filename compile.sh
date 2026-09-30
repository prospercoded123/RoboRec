#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "🔨 Building RoboRec with Nuitka..."
echo "This will take 10-20 minutes on first build"
echo ""

rm -rf dist

# Stamp the build's source commit into the app so a Diagnostics export can say what built it.
.venv/bin/python scripts/write_build_info.py

# Get number of CPU cores for parallel compilation
NUM_CORES=$(nproc 2>/dev/null || echo 4)

.venv/bin/python -m nuitka \
  --assume-yes-for-downloads \
  `# --mingw64 forces Nuitka to download/use its OWN managed toolchain instead of` \
  `# auto-detecting whatever compiler is already on this machine. NOT passed below --` \
  `# auto-detection is faster whenever it already works, and that download is ~267MB.` \
  `# Add a "--mingw64 \" line right after --assume-yes-for-downloads ONLY if a plain` \
  `# build fails with a compiler-arch-mismatch warning followed by "windows.h: No such` \
  `# file or directory" -- see compile.ps1's comment for the full explanation.` \
  --standalone \
  --follow-imports \
  --enable-plugin=pyside6 \
  --include-package=robo_rec \
  --include-package=bip_utils \
  `# bip_utils reads its BIP39 wordlists from data files at runtime (mnemonic checks, Derive Wallet).` \
  --include-package-data=bip_utils \
  --include-package=coincurve \
  --include-package=PySide6 \
  --include-package=Crypto \
  --include-package=py_crypto_hd_wallet \
  --include-package=numpy \
  --include-package=pyopencl \
  --include-package-data=pyopencl \
  --include-data-dir="src/robo_rec/gui/assets=robo_rec/gui/assets" \
  --include-data-dir="vendor=vendor" \
  --windows-icon-from-ico="src/robo_rec/gui/assets/app-icon.ico" \
  --windows-console-mode=disable \
  --output-filename=Roborec \
  --jobs="$NUM_CORES" \
  --lto=auto \
  --output-dir=dist \
  src/robo_rec/main.py

EXECUTABLE=$(find dist -type f \( -name "Roborec.exe" -o -name "Roborec" \) | head -n 1)

if [ -z "$EXECUTABLE" ]; then
  echo "✗ Build failed - executable not found under dist/"
  exit 1
fi

FOLDER=$(dirname "$EXECUTABLE")

# --- Stage 2: compile vendor/btcrecover/seedrecover.py into its own executable. ---
# Roborec.exe never runs recovery itself — it shells out to seedrecover(.exe) (see
# robo_rec.util.paths.seedrecover_command()) and streams its output. Nothing built that
# executable before, which is exactly the "[WinError 2] The system cannot find the file
# specified" (or "No such file or directory" here) failure when clicking Proceed:
# repo_root()/seedrecover was expected but never produced. This stage builds it and
# merges it into the same app folder as Roborec, matching what seedrecover_command() looks for.
echo ""
echo "🔨 Building seedrecover (recovery engine) with Nuitka..."

SEEDRECOVER_BUILD_DIR="$REPO_ROOT/dist/_seedrecover_build"
(
  cd "$REPO_ROOT/vendor/btcrecover"
  "$REPO_ROOT/.venv/bin/python" -m nuitka \
    --assume-yes-for-downloads \
  `# See the main build stage's comment re: --mingw64 -- not passed here either.` \
    --standalone \
    --follow-imports \
    --include-package=btcrecover \
    --include-package=lib \
    `# lib/'s data files are not bundled by --include-package alone, and lib/bitcoinlib` \
    `# reads config/VERSION at import time, so seedrecover.exe died instantly without them.` \
    --include-package-data=lib \
    `# robo_rec_opencl_correctness.py sits next to seedrecover.py, reached only via a` \
    `# conditional import (the sentinel-arg dispatch near the top of seedrecover.py) —` \
    `# explicit rather than trusting --follow-imports to walk into that branch.` \
    --include-module=robo_rec_opencl_correctness \
    --include-package=bip_utils \
    --include-package-data=bip_utils \
    --include-package=coincurve \
    --include-package=Crypto \
    --include-package=py_crypto_hd_wallet \
    --include-package=numpy \
    --include-package=pyopencl \
    --include-package-data=pyopencl \
    --include-package=google.protobuf \
    --include-data-dir="btcrecover/wordlists=btcrecover/wordlists" \
    --include-data-dir="btcrecover/opencl=btcrecover/opencl" \
    --output-filename=seedrecover \
    --jobs="$NUM_CORES" \
    --lto=auto \
    --output-dir="$SEEDRECOVER_BUILD_DIR" \
    seedrecover.py
)

SEEDRECOVER_EXECUTABLE=$(find "$SEEDRECOVER_BUILD_DIR" -type f \( -name "seedrecover.exe" -o -name "seedrecover" \) | head -n 1)
if [ -z "$SEEDRECOVER_EXECUTABLE" ]; then
  echo "✗ seedrecover build failed - executable not found under $SEEDRECOVER_BUILD_DIR"
  echo "Roborec built fine, but recovery will fail until this is fixed."
  exit 1
fi

echo "Merging seedrecover and its dependencies into the app folder..."
SEEDRECOVER_FOLDER=$(dirname "$SEEDRECOVER_EXECUTABLE")
cp -a "$SEEDRECOVER_FOLDER/." "$FOLDER/"
chmod +x "$SEEDRECOVER_EXECUTABLE"

MERGED_SEEDRECOVER="$FOLDER/$(basename "$SEEDRECOVER_EXECUTABLE")"
if [ ! -f "$MERGED_SEEDRECOVER" ]; then
  echo "✗ Merge completed but $(basename "$SEEDRECOVER_EXECUTABLE") is missing from $FOLDER"
  exit 1
fi
chmod +x "$MERGED_SEEDRECOVER"

SIZE=$(du -sh "$FOLDER" | cut -f1)

echo ""
echo "✓ Build successful!"
echo "  Folder to copy:  $FOLDER"
echo "  Main executable: $EXECUTABLE"
echo "  Recovery engine: $MERGED_SEEDRECOVER"
echo "  Total size:      $SIZE"
echo ""
echo "Copy the WHOLE folder above to the flash drive, not just the executable."
chmod +x "$EXECUTABLE"
