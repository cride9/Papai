@echo off
echo Starting PapaiFinal Backend (port 8000)...
start "Papai Backend" cmd /c "python run.py"

echo Starting PapaiFinal Frontend (port 80)...
start "Papai Frontend" cmd /c "cd /d "%~dp0frontend" && python -m http.server 80 --bind 0.0.0.0"

echo.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost
echo.
