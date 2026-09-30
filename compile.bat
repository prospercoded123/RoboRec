@echo off
REM Build RoboRec with Nuitka into a single Windows executable
setlocal enabledelayedexpansion

set "REPO_ROOT=%~dp0"
cd /d "%REPO_ROOT%"

echo.
echo 🔨 Building RoboRec with Nuitka...
echo This will take 10-20 minutes on first build
echo.

if exist dist (
    echo Cleaning old build...
    rmdir /s /q dist
)

REM Stamp the build's source commit into the app so a Diagnostics export can say what built it.
call .venv\Scripts\python.exe scripts\write_build_info.py

for /f %%A in ('powershell -Command "(Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors"') do set NUM_CORES=%%A
echo Starting compilation on %NUM_CORES% cores...
REM --mingw64 forces Nuitka to download/use its OWN managed toolchain instead of
REM auto-detecting whatever compiler is already on this machine. NOT passed below --
REM auto-detection is faster whenever it already works, and that download is ~267MB.
REM Add "--mingw64 ^" as its own line right after --assume-yes-for-downloads ONLY if a
REM plain build fails with a compiler-arch-mismatch warning followed by "windows.h: No
REM such file or directory" -- see compile.ps1's comment for the full explanation.
call .venv\Scripts\python.exe -m nuitka ^
  --assume-yes-for-downloads ^
  --standalone ^
  --follow-imports ^
  --enable-plugin=pyside6 ^
  --include-package=robo_rec ^
  --include-package=bip_utils ^
  --include-package-data=bip_utils ^
  --include-package=coincurve ^
  --include-package=PySide6 ^
  --include-package=Crypto ^
  --include-package=py_crypto_hd_wallet ^
  --include-package=numpy ^
  --include-package=pyopencl ^
  --include-package-data=pyopencl ^
  --include-data-dir="src/robo_rec/gui/assets=robo_rec/gui/assets" ^
  --include-data-dir="vendor=vendor" ^
  --windows-icon-from-ico="src/robo_rec/gui/assets/app-icon.ico" ^
  --windows-console-mode=disable ^
  --output-filename=Roborec.exe ^
  --jobs=%NUM_CORES% ^
  --lto=auto ^
  --output-dir=dist ^
  src/robo_rec/main.py

if %errorlevel% neq 0 (
    echo ✗ Build failed with exit code %errorlevel%
    exit /b %errorlevel%
)

set "EXEPATH="
for /f "delims=" %%F in ('dir /s /b dist\Roborec.exe 2^>nul') do set "EXEPATH=%%F"
if not defined EXEPATH (
    echo ✗ Build completed but Roborec.exe not found under dist\
    exit /b 1
)
for %%F in ("!EXEPATH!") do set "EXEDIR=%%~dpF"

REM --- Stage 2: compile vendor/btcrecover/seedrecover.py into its own executable. ---
REM Roborec.exe never runs recovery itself -- it shells out to seedrecover.exe (see
REM robo_rec.util.paths.seedrecover_command()) and streams its output. Nothing built that
REM executable before, which is exactly the "[WinError 2] The system cannot find the file
REM specified" failure when clicking Proceed: repo_root()\seedrecover.exe was expected but
REM never produced. This stage builds it and merges it into the same app folder as
REM Roborec.exe, matching what seedrecover_command() looks for.
echo.
echo Building seedrecover.exe (recovery engine) with Nuitka...

set "SEEDRECOVER_BUILD_DIR=%REPO_ROOT%dist\_seedrecover_build"
pushd "%REPO_ROOT%vendor\btcrecover"
REM See the main build stage's comment above re: --mingw64 -- not passed here either.
call "%REPO_ROOT%.venv\Scripts\python.exe" -m nuitka ^
  --assume-yes-for-downloads ^
  --standalone ^
  --follow-imports ^
  --include-package=btcrecover ^
  --include-package=lib ^
  --include-package-data=lib ^
  --include-module=robo_rec_opencl_correctness ^
  --include-package=bip_utils ^
  --include-package-data=bip_utils ^
  --include-package=coincurve ^
  --include-package=Crypto ^
  --include-package=py_crypto_hd_wallet ^
  --include-package=numpy ^
  --include-package=pyopencl ^
  --include-package-data=pyopencl ^
  --include-package=google.protobuf ^
  --include-data-dir="btcrecover/wordlists=btcrecover/wordlists" ^
  --include-data-dir="btcrecover/opencl=btcrecover/opencl" ^
  --output-filename=seedrecover.exe ^
  --jobs=%NUM_CORES% ^
  --lto=auto ^
  --output-dir="%SEEDRECOVER_BUILD_DIR%" ^
  seedrecover.py
set "SEEDRECOVER_ERR=%errorlevel%"
popd

if %SEEDRECOVER_ERR% neq 0 (
    echo ✗ seedrecover.exe build failed with exit code %SEEDRECOVER_ERR%
    echo Roborec.exe built fine, but recovery will fail with WinError 2 until this is fixed.
    exit /b %SEEDRECOVER_ERR%
)

set "SEEDRECOVER_EXEPATH="
for /f "delims=" %%F in ('dir /s /b "%SEEDRECOVER_BUILD_DIR%\seedrecover.exe" 2^>nul') do set "SEEDRECOVER_EXEPATH=%%F"
if not defined SEEDRECOVER_EXEPATH (
    echo ✗ seedrecover build completed but seedrecover.exe not found under %SEEDRECOVER_BUILD_DIR%
    exit /b 1
)
for %%F in ("!SEEDRECOVER_EXEPATH!") do set "SEEDRECOVER_EXEDIR=%%~dpF"

echo Merging seedrecover.exe and its dependencies into the app folder...
xcopy "!SEEDRECOVER_EXEDIR!*" "!EXEDIR!" /E /Y /I >nul

if not exist "!EXEDIR!seedrecover.exe" (
    echo ✗ Merge completed but seedrecover.exe is missing from !EXEDIR!
    exit /b 1
)

for /F "usebackq" %%A in ('powershell -Command "(Get-ChildItem -Path '!EXEDIR!' -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB | ForEach-Object { [Math]::Round($_, 1) }"') do set SIZE=%%A
echo.
echo ✓ Build successful!
echo   Folder to copy:  !EXEDIR!
echo   Main executable: !EXEPATH!
echo   Recovery engine: !EXEDIR!seedrecover.exe
echo   Total size:      !SIZE! MB
echo.
echo Copy the WHOLE folder above to the flash drive, not just the .exe.
exit /b 0
