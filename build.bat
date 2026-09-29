@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Single source of truth for the release version. Keep it equal to
rem APP_VERSION in main.py -- the build checks that below rather than
rem trusting it, because a mismatch ships an exe whose About box lies.
set VERSION=0.1.11

echo [1/3] Installing build dependencies...
python -m pip install -r requirements.txt || goto :fail
python -m pip install nuitka ordered-set zstandard || goto :fail

echo [2/3] Generating icon...
python -c "import main,sys; sys.exit(0 if main.APP_VERSION=='%VERSION%' else 1)" || goto :version_mismatch
python make_icon.py || goto :fail
python -c "from comtypes import client; client.GetModule('qedit.dll'); client.GetModule('quartz.dll')" || goto :fail

echo [3/3] Building WebcamMirror_%VERSION%.exe...
python -m nuitka main.py ^
  --mode=onefile ^
  --enable-plugin=pyside6 ^
  --include-package=comtypes.gen ^
  --assume-yes-for-downloads ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=assets\icon.ico ^
  --include-data-dir=assets=assets ^
  --output-dir=dist_nuitka ^
  --output-filename=WebcamMirror_%VERSION%.exe ^
  --noinclude-qt-translations ^
  --include-qt-plugins=platforminputcontexts || goto :fail

if not exist dist mkdir dist
copy /y "dist_nuitka\WebcamMirror_%VERSION%.exe" "dist\WebcamMirror_%VERSION%.exe" >nul || goto :fail

echo.
echo Done -^> dist\WebcamMirror_%VERSION%.exe
goto :eof

:version_mismatch
echo.
echo BUILD FAILED: VERSION in build.bat does not match APP_VERSION in main.py
exit /b 1

:fail
echo.
echo BUILD FAILED
exit /b 1
