@echo off
title MG Apparel HR Chatbot — Local AI
color 0A

echo.
echo  ============================================================
echo    MG Apparel HR Chatbot  ^|  LOCAL AI  ^|  FREE FOREVER
echo  ============================================================
echo.

:: ── Check Python ──────────────────────────────────────────────────────────
set "PYTHON_EXE=python"
python --version >nul 2>&1
if errorlevel 1 (
    :: Fallback: Windows Store/App Execution Alias Python (common on corporate PCs)
    if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe"
    ) else if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python3.13.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Microsoft\WindowsApps\python3.13.exe"
    ) else (
        echo  [ERROR] Python not found!
        echo  Install Python 3.10+ from https://python.org/downloads
        echo  (or enable the Python App Execution Alias in Windows Settings).
        pause
        exit /b 1
    )
)

:: ── Check Ollama (optional) ───────────────────────────────────────────────
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo  [WARNING] Ollama does not appear to be running.
    echo  The chatbot will still start, but answers will show an offline warning.
    echo  Start Ollama with "ollama serve" (and install a model e.g. "ollama pull llama3.2").
) else (
    echo  [OK] Ollama is running.
)

:: ── Set working directory ──────────────────────────────────────────────────
cd /d "%~dp0"

:: ── Create / use local venv (portable across PCs) ──────────────────────────
set "VENV_DIR=.venv"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo  Creating local virtual environment (%VENV_DIR%)...
    "%PYTHON_EXE%" -m venv "%VENV_DIR%"
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

:: ── Launch ────────────────────────────────────────────────────────────────
echo.
echo  Starting HR Chatbot...
echo  Open your browser at: http://localhost:5000  (or the port shown in the console if 5000 is in use)
echo  Press Ctrl+C to stop the server.
echo.
"%PY%" hr_chatbot.py

pause
