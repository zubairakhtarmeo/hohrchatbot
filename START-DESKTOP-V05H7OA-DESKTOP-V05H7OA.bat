@echo off
setlocal EnableExtensions
title MG Apparel HR Chatbot — Local AI
color 0A

echo.
echo  ============================================================
echo    MG Apparel HR Chatbot  ^|  LOCAL AI  ^|  FREE FOREVER
echo  ============================================================
echo.

:: ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 goto :no_python

:: ── Check Ollama ───────────────────────────────────────────────────────────
curl.exe -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 goto :no_ollama

echo  [OK] Ollama is running.

:: ── Set working directory ──────────────────────────────────────────────────
cd /d "%~dp0"

:: ── Force default port (change if needed) ─────────────────────────────────
:: If PORT is already set in the environment, we keep it.
if "%PORT%"=="" set "PORT=5001"

:: ── Create / use local venv (portable across PCs) ──────────────────────────
set "VENV_DIR=.venv"
if not exist "%VENV_DIR%\Scripts\python.exe" goto :make_venv
set "PY=%VENV_DIR%\Scripts\python.exe"

:: ── Install Python dependencies ────────────────────────────────────────────
echo.
echo  Checking Python dependencies...
"%PY%" -m pip show chromadb >nul 2>&1
if errorlevel 1 goto :deps
echo  [OK] Dependencies ready.

:: ── Download embedding model if needed ────────────────────────────────────
echo  [INFO] Embedding model will auto-download on first run (~90 MB).
echo         After that it works fully offline.

:: ── Default Ollama model (can override in environment) ───────────────────
if "%OLLAMA_MODEL%"=="" set "OLLAMA_MODEL=llama3.2"

:: ── Launch ────────────────────────────────────────────────────────────────
echo.
echo  Starting HR Chatbot...
echo  Open your browser at: http://localhost:%PORT%
echo.
echo  NOTE: If other devices can't connect, allow Python through Windows Firewall
echo        or open TCP port %PORT% for Private networks.
echo        Optional (run as Admin):
echo        netsh advfirewall firewall add rule name="HR Chatbot" dir=in action=allow protocol=TCP localport=%PORT% profile=private
echo  Press Ctrl+C to stop the server.
echo.
"%PY%" hr_chatbot.py

pause

goto :eof

:no_python
echo  [ERROR] Python not found!
echo  Please install Python 3.10+ from https://python.org/downloads
echo  Make sure to check "Add Python to PATH" during install.
pause
exit /b 1

:no_ollama
echo  [WARNING] Ollama does not appear to be running.
echo  Start it from the system tray or run: ollama serve
echo  If you haven't downloaded the model yet, run: ollama pull llama3.2
echo  (If you get a memory allocation error, try: ollama pull llama3.2:1b)
echo  Then run this launcher again.
pause
exit /b 1

:make_venv
echo.
echo  Creating local virtual environment (%VENV_DIR%)...
python -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo  [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)
set "PY=%VENV_DIR%\Scripts\python.exe"
goto :deps_check

:deps
echo  Installing dependencies (first-time only, may take a few minutes)...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo  [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
goto :deps_done

:deps_check
"%PY%" -m pip show chromadb >nul 2>&1
if errorlevel 1 goto :deps

:deps_done
echo  [OK] Dependencies ready.
goto :after_deps

:after_deps
echo  [INFO] Embedding model will auto-download on first run (~90 MB).
echo         After that it works fully offline.

:: ── Default Ollama model (can override in environment) ───────────────────
if "%OLLAMA_MODEL%"=="" set "OLLAMA_MODEL=llama3.2"
echo.
echo  Starting HR Chatbot...
echo  Open your browser at: http://localhost:%PORT%
echo  Press Ctrl+C to stop the server.
echo.
"%PY%" hr_chatbot.py
pause
