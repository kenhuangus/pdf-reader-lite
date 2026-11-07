@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo Building PDF Lite Windows Installer (MSI)...
echo ============================================

set WIX_DIR=%~dp0tools\wix
if not exist "%WIX_DIR%\candle.exe" (
  echo [INFO] WiX toolset not found. Downloading WiX 3.11 standalone binaries...
  if not exist "%~dp0tools" mkdir "%~dp0tools"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/wixtoolset/wix3/releases/download/wix3112rtm/wix311-binaries.zip' -OutFile '%~dp0tools\wix311.zip'; Expand-Archive -Path '%~dp0tools\wix311.zip' -DestinationPath '%WIX_DIR%' -Force; Remove-Item '%~dp0tools\wix311.zip'"
)

if not exist "%WIX_DIR%\candle.exe" (
  echo Error: WiX binaries not found in %WIX_DIR%.
  exit /b 1
)

if not exist "%~dp0dist\PDF Lite.exe" (
  echo Building executable first...
  call "%~dp0build_exe.bat"
  if %ERRORLEVEL% neq 0 (
    echo Error: Failed to build PDF Lite.exe
    exit /b %ERRORLEVEL%
  )
)

if not exist "%~dp0dist" mkdir "%~dp0dist"

cd /d "%~dp0installer"

echo Compiling WiX Product definition...
"%WIX_DIR%\candle.exe" -nologo -arch x64 -ext "%WIX_DIR%\WixUIExtension.dll" -ext "%WIX_DIR%\WixUtilExtension.dll" Product.wxs -out Product.wixobj
if %ERRORLEVEL% neq 0 (
  echo Error: candle compilation failed.
  exit /b %ERRORLEVEL%
)

echo Linking Windows Installer MSI package...
"%WIX_DIR%\light.exe" -nologo -ext "%WIX_DIR%\WixUIExtension.dll" -ext "%WIX_DIR%\WixUtilExtension.dll" -sval Product.wixobj -out "%~dp0dist\pdf-lite.msi"
if %ERRORLEVEL% neq 0 (
  echo Error: light linker failed.
  exit /b %ERRORLEVEL%
)

echo Cleaning intermediate build objects...
del Product.wixobj 2>nul
del "%~dp0dist\pdf-lite.wixpdb" 2>nul

echo.
echo ============================================
echo MSI Build Successful!
echo Output: %~dp0dist\pdf-lite.msi
echo ============================================

endlocal
