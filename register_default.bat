@echo off
setlocal
cd /d "%~dp0"

echo Registering PDF Lite in Windows...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$progId = 'PDFLite.Document'; " ^
  "$exe = '%~dp0dist\PDF Lite.exe'; " ^
  "$script = '%~dp0pdf_reader_lite.py'; " ^
  "$icon = '%~dp0assets\icon.ico'; " ^
  "$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; " ^
  "if (-not $pythonw) { $pythonw = (Get-Command python.exe -ErrorAction SilentlyContinue).Source } " ^
  "if (-not $pythonw) { $pythonw = 'C:\Users\kenhu\AppData\Local\Programs\Python\Python313\pythonw.exe' } " ^
  "if (Test-Path $exe) { " ^
  "  $cmd = ('\"' + $exe + '\" \"%%1\"'); " ^
  "  $iconSpec = ('\"' + $exe + '\",0'); " ^
  "} else { " ^
  "  $cmd = ('\"' + $pythonw + '\" \"' + $script + '\" \"%%1\"'); " ^
  "  $iconSpec = ('\"' + $icon + '\",0'); " ^
  "} " ^
  "New-Item -Path 'HKCU:\Software\Classes\$progId' -Force -Value 'PDF Lite Document' | Out-Null; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\Classes\$progId' -Name 'FriendlyTypeName' -Value 'PDF Lite Document'; " ^
  "New-Item -Path 'HKCU:\Software\Classes\$progId\DefaultIcon' -Force -Value $iconSpec | Out-Null; " ^
  "Set-Item -Path \"HKCU:\Software\Classes\$progId\shell\open\command\" -Value $cmd; " ^
  "New-Item -Path 'HKCU:\Software\Classes\.pdf\OpenWithProgids' -Force | Out-Null; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\Classes\.pdf\OpenWithProgids' -Name $progId -Value ([byte[]]@()); " ^
  "New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithProgids' -Force | Out-Null; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithProgids' -Name $progId -Value ([byte[]]@()); " ^
  "New-Item -Path 'HKCU:\Software\Classes\Applications\PDF Lite.exe\shell\open\command' -Force | Out-Null; " ^
  "Set-Item -Path 'HKCU:\Software\Classes\Applications\PDF Lite.exe\shell\open\command' -Value $cmd; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\Classes\Applications\PDF Lite.exe' -Name 'FriendlyAppName' -Value 'PDF Lite'; " ^
  "New-Item -Path 'HKCU:\Software\PDF Lite\Capabilities\FileAssociations' -Force | Out-Null; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\PDF Lite\Capabilities' -Name 'ApplicationName' -Value 'PDF Lite'; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\PDF Lite\Capabilities' -Name 'ApplicationDescription' -Value 'Lightweight Acrobat-style PDF reader'; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\PDF Lite\Capabilities\FileAssociations' -Name '.pdf' -Value $progId; " ^
  "New-Item -Path 'HKCU:\Software\RegisteredApplications' -Force | Out-Null; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\RegisteredApplications' -Name 'PDF Lite' -Value 'Software\PDF Lite\Capabilities'; " ^
  "Write-Host 'PDF Lite executable is registered as an official PDF handler in Windows.' -ForegroundColor Green"

echo.
echo Launching Windows Default Apps settings so you can set PDF Lite as default...
start ms-settings:defaultapps

endlocal
