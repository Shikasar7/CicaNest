@echo off
rem Stop the background nest service.
cd /d "%~dp0"
uv run --frozen nest stop
pause
