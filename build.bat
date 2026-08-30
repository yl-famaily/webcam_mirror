@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt || goto :fail
python -m pip install pyinstaller || goto :fail

echo [2/3] Generating icon...
python make_icon.py || goto :fail

echo [3/3] Building WebcamMirror_0.1.0.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name WebcamMirror_0.1.0 ^
  --icon assets/icon.ico ^
  --add-data "assets;assets" ^
  --collect-binaries onnxruntime ^
  --noupx ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module matplotlib ^
  main.py || goto :fail

echo.
echo Done -^> dist\WebcamMirror_0.1.0.exe
goto :eof

:fail
echo.
echo BUILD FAILED
exit /b 1
