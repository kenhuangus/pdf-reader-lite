@echo off
setlocal
cd /d "%~dp0"

echo Unregistering PDF Lite from Windows...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Remove-Item -Path 'HKCU:\Software\Classes\PDFLite.Document' -Recurse -ErrorAction SilentlyContinue; " ^
  "Remove-ItemProperty -Path 'HKCU:\Software\Classes\.pdf\OpenWithProgids' -Name 'PDFLite.Document' -ErrorAction SilentlyContinue; " ^
  "Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithProgids' -Name 'PDFLite.Document' -ErrorAction SilentlyContinue; " ^
  "Remove-Item -Path 'HKCU:\Software\Classes\Applications\PDFLite.exe' -Recurse -ErrorAction SilentlyContinue; " ^
  "Remove-Item -Path 'HKCU:\Software\PDF Lite' -Recurse -ErrorAction SilentlyContinue; " ^
  "Remove-ItemProperty -Path 'HKCU:\Software\RegisteredApplications' -Name 'PDF Lite' -ErrorAction SilentlyContinue; " ^
  "Write-Host 'PDF Lite registration removed.' -ForegroundColor Yellow"

endlocal
