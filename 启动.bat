@echo off
rem Start nest in the background and open the console.
cd /d "%~dp0"
uv run --frozen nest start
if errorlevel 1 pause
