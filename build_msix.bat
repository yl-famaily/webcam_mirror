@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem NOTE: keep this file ASCII-only. With "chcp 65001" active, cmd loses its
rem byte offset while parsing a batch file that contains multi-byte characters
rem and starts chopping the first word off later lines -- "python foo.py" runs
rem as "foo.py". Korean belongs in the .py files, not here.

set VERSION=0.1.10.0
set OUT=dist_store\WebcamMirror_%VERSION%_x64.msix

rem Do not expand %ProgramFiles(x86)% inside a parenthesised block: the closing
rem paren in the value terminates the block and breaks parsing. Capture it here.
set "SDKBIN=%ProgramFiles(x86)%\Windows Kits\10\bin"

echo [1/6] Installing dependencies...
python -m pip install -r requirements.txt || goto :fail
python -m pip install pyinstaller || goto :fail

echo [2/6] Generating icon...
python make_icon.py || goto :fail

echo [3/6] Generating Store tile assets...
python make_store_assets.py || goto :fail

echo [4/6] Building onedir layout...
python -c "from comtypes import client; client.GetModule('qedit.dll'); client.GetModule('quartz.dll')" || goto :fail
rem MSIX must not use onefile: it would unpack ~300MB to a temp folder on every
rem launch, and the payload would be invisible to Store certification tooling.
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name WebcamMirror ^
  --icon assets/icon.ico ^
  --add-data "assets;assets" ^
  --collect-binaries onnxruntime ^
  --collect-submodules comtypes.gen ^
  --noupx ^
  --distpath dist_msix ^
  --workpath build_msix ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.QtNetwork ^
  --exclude-module matplotlib ^
  main.py || goto :fail

echo [5/6] Staging package layout...
if exist staging_msix rmdir /s /q staging_msix
mkdir staging_msix || goto :fail
xcopy /e /i /q /y "dist_msix\WebcamMirror\*" "staging_msix\" >nul || goto :fail
copy /y "packaging\AppxManifest.xml" "staging_msix\" >nul || goto :fail
xcopy /e /i /q /y "packaging\Assets" "staging_msix\Assets\" >nul || goto :fail

echo [6/6] Packing MSIX...
rem Pick the highest installed SDK.
set MAKEAPPX=
for /f "delims=" %%D in ('dir /b /ad /on "!SDKBIN!\10.*" 2^>nul') do (
  if exist "!SDKBIN!\%%D\x64\makeappx.exe" set "MAKEAPPX=!SDKBIN!\%%D\x64\makeappx.exe"
)
if not defined MAKEAPPX (
  echo.
  echo makeappx.exe not found. Install the Windows SDK:
  echo     winget install Microsoft.WindowsSDK.10.0.18362
  goto :fail
)
echo     using "!MAKEAPPX!"

if not exist dist_store mkdir dist_store
"!MAKEAPPX!" pack /d staging_msix /p "%OUT%" /o >nul || goto :fail

echo.
echo Done -^> %OUT%
echo.
echo The package is unsigned. Partner Center re-signs it on upload, so submit
echo it as is. Installing it locally for testing needs your own signature.
goto :eof

:fail
echo.
echo BUILD FAILED
exit /b 1
