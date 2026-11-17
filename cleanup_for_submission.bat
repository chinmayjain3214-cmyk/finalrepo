@echo off
cd /d "%~dp0"
echo Moving files not needed for submission into _removed_before_submission ...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" cleanup_for_submission.py
) else (
    python cleanup_for_submission.py
)
pause
