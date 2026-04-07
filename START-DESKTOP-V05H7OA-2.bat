@echo off
title MG Apparel HR Chatbot — Local AI
color 0A

echo.
echo  ============================================================
echo    MG Apparel HR Chatbot  ^|  LOCAL AI  ^|  FREE FOREVER
echo  ============================================================
echo.

:: ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found!
    echo  Please install Python 3.10+ from https://python.org/downloads
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: ── Check Ollama ───────────────────────────────────────────────────────────
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo  [WARNING] Ollama does not appear to be running.
    echo.
    echo  If you haven't installed Ollama yet:
    echo    1. Download from https://ollama.com/download
    echo    2. Install and run it
    echo    3. Open a new terminal and run:  ollama pull llama3.2
    echo       (If you get a memory allocation error, try: ollama pull llama3.2:1b)
    echo    4. Then come back and run this file again.
    echo.
    echo  If Ollama IS installed, please start it from your system tray
    echo  or run "ollama serve" in another terminal window.
    echo.
    pause
    exit /b 1
)

echo  [OK] Ollama is running.

:: ── Set working directory ──────────────────────────────────────────────────
cd /d "%~dp0"

:: ── Create / use local venv (portable across PCs) ──────────────────────────
set "VENV_DIR=.venv"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo  Creating local virtual environment (%VENV_DIR%)...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)
set "PY=%VENV_DIR%\Scripts\python.exe"

:: ── Install Python dependencies ────────────────────────────────────────────
echo.
echo  Checking Python dependencies...
"%PY%" -m pip show chromadb >nul 2>&1
if errorlevel 1 (
    echo  Installing dependencies (first-time only, may take a few minutes)...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo  [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)
echo  [OK] Dependencies ready.

:: ── Download embedding model if needed ────────────────────────────────────
echo  [INFO] Embedding model will auto-download on first run (~90 MB).
echo         After that it works fully offline.

:: ── Default Ollama model (can override in environment) ───────────────────
if "%OLLAMA_MODEL%"=="" set "OLLAMA_MODEL=llama3.2"

:: ── Launch ────────────────────────────────────────────────────────────────
echo.
echo  Starting HR Chatbot...
echo  Open your browser at: http://localhost:5000  (or the port shown in the console if 5000 is in use)
echo  Press Ctrl+C to stop the server.
echo.
"%PY%" hr_chatbot.py

pause
