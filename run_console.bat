@echo off
cd /d "%~dp0"
python pdf_reader_lite.py %*
if errorlevel 1 pause
