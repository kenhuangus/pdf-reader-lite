@echo off
setlocal
cd /d "%~dp0"

if not exist "pdf_reader_lite.py" (
  echo Could not find pdf_reader_lite.py in %CD%.
  pause
  exit /b 1
)

set "PYTHONW="
where pythonw >nul 2>&1 && set "PYTHONW=pythonw"
if not defined PYTHONW where pyw >nul 2>&1 && set "PYTHONW=pyw"
if not defined PYTHONW set "PYTHONW=python"

if "%~1"=="" (
  start "" "%PYTHONW%" "%~dp0pdf_reader_lite.py"
) else (
  start "" "%PYTHONW%" "%~dp0pdf_reader_lite.py" "%~1"
)
endlocal
