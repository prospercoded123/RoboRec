# Build RoboRec with Nuitka as a standalone folder (Roborec.dist\Roborec.exe)
param(
    [switch]$Clean = $true
)

$ErrorActionPreference = "Stop"

$REPO_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $REPO_ROOT

# Nuitka silently falls back to the Zig compiler when it finds no usable MSVC/MinGW
# ("No usable C compiler, attempt fallback to zig" in its verbose log). Zig's `cc` defaults
# to the BUILD machine's own CPU (it defined __AVX2__ and __AVX512F__ on an i5-1135G7), so
# the resulting .exe crashes with an illegal-instruction error on any PC whose CPU lacks
# those instructions. MSVC and MinGW emit baseline x86-64 code, which runs everywhere.
# Nuitka writes scons-report.txt (naming the compiler it chose) before the long C compile,
# so this check reads it afterward and refuses to hand out a non-portable build.
# Set ROBOREC_ALLOW_ZIG=1 to override for a local-only test build.
function Assert-PortableCompiler([string]$ReportPath, [string]$Stage) {
    if ($env:ROBOREC_ALLOW_ZIG -eq "1") { return }
    if (-not (Test-Path $ReportPath)) {
        Write-Host "Could not find $ReportPath to verify the $Stage compiler; build may not be portable." -ForegroundColor Yellow
        return
    }
    if (Select-String -Path $ReportPath -Pattern 'zig\.exe' -Quiet) {
        Write-Host ""
        Write-Host "The $Stage stage was compiled with Zig, which targets THIS machine's CPU." -ForegroundColor Red
        Write-Host "The result can crash on other PCs (illegal instruction). Install a real compiler and rebuild:" -ForegroundColor Red
        Write-Host "  - Visual Studio Build Tools with the 'Desktop development with C++' workload (preferred), or" -ForegroundColor Red
        Write-Host "  - uncomment ""--mingw64"" in this script to use Nuitka's own MinGW64 download." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Building RoboRec with Nuitka..." -ForegroundColor Green
Write-Host "This will take 10-20 minutes on first build" -ForegroundColor Yellow
Write-Host ""

if ($Clean -and (Test-Path "dist")) {
    Write-Host "Cleaning old build..." -ForegroundColor Gray
    Remove-Item -Recurse -Force dist
}

# --mingw64 (below) is rejected outright by Nuitka on Python 3.13+: "MinGW64 is not currently
# supported with Python 3.13". Fail now with the fix instead of 20 minutes of setup first.
$pyVersion = & .venv\Scripts\python.exe -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyVersion -ge [version]"3.13") {
    Write-Host "This venv is Python $pyVersion, which Nuitka's --mingw64 does not support." -ForegroundColor Red
    Write-Host "Recreate it on Python 3.11 or 3.12 (e.g. 'uv venv --python 3.12' then 'uv sync')," -ForegroundColor Red
    Write-Host "or install Visual Studio Build Tools and remove --mingw64 from this script." -ForegroundColor Red
    exit 1
}

# Stamp the build's source commit into the app so a Diagnostics export can say what built it.
& .venv\Scripts\python.exe scripts\write_build_info.py

# Nuitka's own cache (downloaded MinGW64 toolchain included) defaults to
# appdirs.user_cache_dir("Nuitka"), which under a Microsoft Store Python install resolves
# into that package's virtualized, deeply-nested AppData folder (something like
# ...\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_<hash>\LocalCache\...).
# The downloaded MinGW64's own header path nested inside that is long enough that gcc's
# `#include <windows.h>` silently failed to resolve ("No such file or directory") even
# though the file genuinely existed on disk (confirmed directly) — a Windows path-length
# problem, not a missing or corrupt download. Pointing NUITKA_CACHE_DIR at a short, plain
# path sidesteps it entirely.
$env:NUITKA_CACHE_DIR = "C:\NuitkaCache"

$numCores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
Write-Host "Starting compilation on $numCores cores..." -ForegroundColor Cyan
$nuitkaArgs = @(
    "-m"
    "nuitka"
    "--assume-yes-for-downloads"

    # --mingw64 forces Nuitka to download and use its OWN managed MinGW64 toolchain
    # instead of auto-detecting whatever gcc is already on this machine (e.g. a
    # system-installed TDM-GCC-64). Left DISABLED (commented out) by default because that
    # download is ~267MB from GitHub and was unreliable on this machine's connection
    # (repeated mid-download resets); auto-detection is faster whenever it already works.
    #
    # ENABLE THIS (uncomment "--mingw64" below) only if a plain build fails with something
    # like:
    #   Nuitka-Scons: Mismatch between Python binary (...) and C compiler (...) arches,
    #   that compiler is ignored!
    #   ...fatal error: windows.h: No such file or directory
    # That means Nuitka's own arch probe (objdump on the compiler's .exe) decided the
    # detected gcc doesn't match — confirmed directly on this machine's TDM-GCC-64, whose
    # gcc.exe binary really does report as the 32-bit "pei-i386" PE format even though it
    # correctly targets/produces 64-bit output (verified: it compiles a windows.h-using
    # 64-bit test program fine standalone) — Nuitka's safety check rejects it anyway, and
    # partway into the real build ends up reaching for it regardless, causing the above
    # error. --mingw64 sidesteps that by using a toolchain Nuitka trusts outright.
    "--mingw64"
    "--include-windows-runtime-dlls=yes"
    "--standalone"
    "--follow-imports"
    "--enable-plugin=pyside6"
    "--include-package=robo_rec"
    "--include-package=bip_utils"
    # bip_utils reads its BIP39 wordlists from data files at runtime (mnemonic checks, Derive Wallet).
    "--include-package-data=bip_utils"
    "--include-package=coincurve"
    "--include-package=PySide6"
    "--include-package=Crypto"
    "--include-package=py_crypto_hd_wallet"
    "--include-package=numpy"
    "--include-package=pyopencl"
    "--include-package-data=pyopencl"
    "--include-data-dir=src/robo_rec/gui/assets=robo_rec/gui/assets"
    "--include-data-dir=vendor=vendor"
    "--windows-icon-from-ico=src/robo_rec/gui/assets/app-icon.ico"
    "--windows-console-mode=disable"
    "--output-filename=Roborec.exe"
    "--jobs=$numCores"
    "--lto=auto"
    "--output-dir=dist"
    "src/robo_rec/main.py"
)

& .venv\Scripts\python.exe @nuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

$exe = Get-ChildItem -Path dist -Recurse -Filter "Roborec.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) {
    Write-Host "Build completed but Roborec.exe not found under dist\" -ForegroundColor Red
    exit 1
}
$appFolder = $exe.Directory.FullName
Assert-PortableCompiler (Join-Path $REPO_ROOT "dist\main.build\scons-report.txt") "Roborec.exe"

# --- Stage 2: compile vendor/btcrecover/seedrecover.py into its own executable. ---
# Roborec.exe never runs recovery itself — it shells out to seedrecover.exe (see
# robo_rec.util.paths.seedrecover_command()) and streams its output. Nothing built that
# executable before, which is exactly the "[WinError 2] The system cannot find the file
# specified" failure when clicking Proceed: repo_root()/seedrecover.exe was expected but
# never produced. This stage builds it and merges it into the same app folder as
# Roborec.exe, matching what seedrecover_command() looks for.
Write-Host ""
Write-Host "Building seedrecover.exe (recovery engine) with Nuitka..." -ForegroundColor Green

$seedrecoverBuildDir = Join-Path $REPO_ROOT "dist\_seedrecover_build"
Push-Location (Join-Path $REPO_ROOT "vendor\btcrecover")
try {
    $seedrecoverArgs = @(
        "-m"
        "nuitka"
        "--assume-yes-for-downloads"

        # See the main build stage's comment above (same flag, same reasoning) — disabled
        # by default, only uncomment if a plain build fails with a
        # "windows.h: No such file or directory" / compiler-arch-mismatch error.
        "--mingw64"
        "--include-windows-runtime-dlls=yes"
        "--standalone"
        "--follow-imports"
        "--include-package=btcrecover"
        "--include-package=lib"
        # --include-package alone bundles lib/'s modules but none of its data files, and
        # lib/bitcoinlib reads them at import time (config.py's
        # BITCOINLIB_VERSION = Path(BCL_INSTALL_DIR, 'config/VERSION').open()), so every
        # compiled seedrecover.exe died instantly with "FileNotFoundError: ...
        # lib\bitcoinlib\config\VERSION" before searching a single candidate. Also covers
        # lib/opencl_brute's kernels, needed for --enable-opencl. ~2MB total.
        "--include-package-data=lib"
        # robo_rec_opencl_correctness.py sits next to seedrecover.py and is only
        # reached via a conditional import inside an `if` block (see the sentinel-arg
        # dispatch near the top of seedrecover.py) — explicit, rather than trusting
        # --follow-imports's static analysis to walk into a conditional branch.
        "--include-module=robo_rec_opencl_correctness"
        "--include-package=bip_utils"
        # The engine's Solana path decodes mnemonics via bip_utils' wordlists too.
        "--include-package-data=bip_utils"
        "--include-package=coincurve"
        "--include-package=Crypto"
        "--include-package=py_crypto_hd_wallet"
        "--include-package=numpy"
        "--include-package=pyopencl"
        "--include-package-data=pyopencl"
        "--include-package=google.protobuf"
        "--include-data-dir=btcrecover/wordlists=btcrecover/wordlists"
        "--include-data-dir=btcrecover/opencl=btcrecover/opencl"
        "--output-filename=seedrecover.exe"
        "--jobs=$numCores"
        "--lto=auto"
        "--output-dir=$seedrecoverBuildDir"
        "seedrecover.py"
    )
    & "$REPO_ROOT\.venv\Scripts\python.exe" @seedrecoverArgs
    $seedrecoverExit = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($seedrecoverExit -ne 0) {
    Write-Host "seedrecover.exe build failed with exit code $seedrecoverExit" -ForegroundColor Red
    Write-Host "Roborec.exe built fine, but recovery will fail with WinError 2 until this is fixed." -ForegroundColor Yellow
    exit $seedrecoverExit
}

$seedrecoverExe = Get-ChildItem -Path $seedrecoverBuildDir -Recurse -Filter "seedrecover.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $seedrecoverExe) {
    Write-Host "seedrecover build completed but seedrecover.exe not found under $seedrecoverBuildDir" -ForegroundColor Red
    exit 1
}

Assert-PortableCompiler (Join-Path $seedrecoverBuildDir "seedrecover.build\scons-report.txt") "seedrecover.exe"

Write-Host "Merging seedrecover.exe and its dependencies into the app folder..." -ForegroundColor Cyan
robocopy $seedrecoverExe.Directory.FullName $appFolder /E /IS /IT | Out-Null

$mergedSeedrecover = Join-Path $appFolder "seedrecover.exe"
if (-not (Test-Path $mergedSeedrecover)) {
    Write-Host "Merge completed but seedrecover.exe is missing from $appFolder" -ForegroundColor Red
    exit 1
}

$folderSize = (Get-ChildItem -Path $appFolder -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host "Build successful!" -ForegroundColor Green
Write-Host "  Folder to copy:  $appFolder" -ForegroundColor Green
Write-Host "  Main executable: $($exe.FullName)" -ForegroundColor Green
Write-Host "  Recovery engine: $mergedSeedrecover" -ForegroundColor Green
Write-Host "  Total size: $([math]::Round($folderSize, 1)) MB" -ForegroundColor Green
Write-Host ""
Write-Host "Copy the WHOLE folder above to the flash drive, not just the .exe." -ForegroundColor Yellow
exit 0
