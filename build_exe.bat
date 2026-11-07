@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo Building PDF Lite standalone executable...
echo ============================================

if not exist ".venv_build" (
  echo Creating isolated build environment...
  where uv >nul 2>&1
  if %ERRORLEVEL% equ 0 (
    uv venv .venv_build
    uv pip install -r requirements.txt pyinstaller --python .\.venv_build\Scripts\python.exe
  ) else (
    python -m venv .venv_build
    .\.venv_build\Scripts\pip.exe install -r requirements.txt pyinstaller
  )
)

echo Packaging PDF Lite with PyInstaller and embedded icon...
.\.venv_build\Scripts\pyinstaller.exe "PDF Lite.spec" --clean --noconfirm

if %ERRORLEVEL% equ 0 (
  echo.
  echo ============================================
  echo Build successful!
  echo Output: %~dp0dist\PDF Lite.exe
  echo ============================================
) else (
  echo.
  echo Build failed with error code %ERRORLEVEL%.
)

endlocal
