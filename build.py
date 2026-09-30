#!/usr/bin/env python3
"""Build robo-rec into a standalone folder using Nuitka."""
import shutil
import subprocess
import sys
from pathlib import Path


def build():
    repo_root = Path(__file__).parent
    dist = repo_root / "dist"

    # Clean previous builds
    if dist.exists():
        shutil.rmtree(dist)

    # Stamp the build's source commit into the app so a Diagnostics export can say what built it.
    subprocess.run([sys.executable, str(repo_root / "scripts" / "write_build_info.py")], check=False)

    # Run Nuitka compilation with minimal but complete flags
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--assume-yes-for-downloads",
        # --mingw64 forces Nuitka to download/use its OWN managed toolchain instead of
        # auto-detecting whatever compiler is already on this machine. NOT passed below —
        # auto-detection is faster whenever it already works, and that download is ~267MB.
        # Add "--mingw64" back ONLY if a plain build fails with a compiler-arch-mismatch
        # warning followed by "windows.h: No such file or directory" — see compile.ps1's
        # comment for the full explanation of that failure mode.
        # "--mingw64",
        "--standalone",
        "--follow-imports",
        "--enable-plugin=pyside6",
        "--include-package=robo_rec",
        "--include-package=bip_utils",
        # bip_utils reads its BIP39 wordlists from data files at runtime (mnemonic checks, Derive Wallet).
        "--include-package-data=bip_utils",
        "--include-package=coincurve",
        "--include-package=PySide6",
        "--include-package=numpy",
        "--include-package=pyopencl",
        "--include-package-data=pyopencl",
        "--include-data-dir=src/robo_rec/gui/assets=robo_rec/gui/assets",
        "--windows-icon-from-ico=src/robo_rec/gui/assets/app-icon.ico",
        "--windows-console-mode=disable",
        "--output-filename=Roborec.exe",
        "--output-dir=dist",
        str(repo_root / "src" / "robo_rec" / "main.py"),
    ]

    print("Building with Nuitka...")
    print("This may take 10-20 minutes on first build...\n")
    result = subprocess.run(cmd, cwd=repo_root, check=False)

    if result.returncode != 0:
        sys.exit(result.returncode)

    matches = list(dist.rglob("Roborec.exe")) or list(dist.rglob("Roborec"))
    if not matches:
        print("\n✗ Build completed but Roborec.exe not found under dist/")
        sys.exit(1)

    app_folder = matches[0].parent
    seedrecover_exit = _build_seedrecover(repo_root)
    if seedrecover_exit != 0:
        print(f"\n✗ seedrecover.exe build failed with exit code {seedrecover_exit}")
        print("Roborec.exe built fine, but recovery will fail with WinError 2 until this is fixed.")
        sys.exit(seedrecover_exit)

    seedrecover_dist = repo_root / "dist" / "_seedrecover_build"
    seedrecover_matches = list(seedrecover_dist.rglob("seedrecover.exe"))
    if not seedrecover_matches:
        print(f"\n✗ seedrecover build completed but seedrecover.exe not found under {seedrecover_dist}")
        sys.exit(1)

    print("Merging seedrecover.exe and its dependencies into the app folder...")
    shutil.copytree(seedrecover_matches[0].parent, app_folder, dirs_exist_ok=True)

    merged_seedrecover = app_folder / "seedrecover.exe"
    if not merged_seedrecover.exists():
        print(f"\n✗ Merge completed but seedrecover.exe is missing from {app_folder}")
        sys.exit(1)

    total_size = sum(f.stat().st_size for f in app_folder.rglob("*") if f.is_file())
    print(f"\n✓ Build successful!")
    print(f"  Folder to copy:  {app_folder}")
    print(f"  Main executable: {matches[0]}")
    print(f"  Recovery engine: {merged_seedrecover}")
    print(f"  Total size:      {total_size / (1024**2):.1f} MB")
    print("\nCopy the WHOLE folder above to the flash drive, not just the executable.")
    sys.exit(0)


def _build_seedrecover(repo_root: Path) -> int:
    """Stage 2: compile vendor/btcrecover/seedrecover.py into its own executable.

    Roborec.exe never runs recovery itself — it shells out to seedrecover.exe (see
    robo_rec.util.paths.seedrecover_command()) and streams its output. Nothing built that
    executable before, which is exactly the "[WinError 2] The system cannot find the file
    specified" failure when clicking Proceed: repo_root()/seedrecover.exe was expected but
    never produced.
    """
    print("\nBuilding seedrecover.exe (recovery engine) with Nuitka...")
    btcrecover_dir = repo_root / "vendor" / "btcrecover"
    output_dir = repo_root / "dist" / "_seedrecover_build"

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--assume-yes-for-downloads",
        # See the main build stage's comment re: --mingw64 — not passed here either.
        # "--mingw64",
        "--standalone",
        "--follow-imports",
        "--include-package=btcrecover",
        "--include-package=lib",
        # lib/'s data files aren't bundled by --include-package alone, and lib/bitcoinlib
        # reads config/VERSION at import time, so seedrecover.exe died instantly without
        # them. Also covers lib/opencl_brute's kernels, needed for --enable-opencl.
        "--include-package-data=lib",
        # robo_rec_opencl_correctness.py sits next to seedrecover.py, reached only via
        # a conditional import (the sentinel-arg dispatch near the top of
        # seedrecover.py) — explicit rather than trusting --follow-imports to walk
        # into that branch.
        "--include-module=robo_rec_opencl_correctness",
        "--include-package=bip_utils",
        # The engine's Solana path decodes mnemonics via bip_utils' wordlists too.
        "--include-package-data=bip_utils",
        "--include-package=coincurve",
        "--include-package=Crypto",
        "--include-package=py_crypto_hd_wallet",
        "--include-package=numpy",
        "--include-package=pyopencl",
        "--include-package-data=pyopencl",
        "--include-package=google.protobuf",
        "--include-data-dir=btcrecover/wordlists=btcrecover/wordlists",
        "--include-data-dir=btcrecover/opencl=btcrecover/opencl",
        "--output-filename=seedrecover.exe",
        f"--output-dir={output_dir}",
        "seedrecover.py",
    ]
    result = subprocess.run(cmd, cwd=btcrecover_dir, check=False)
    return result.returncode


if __name__ == "__main__":
    build()
