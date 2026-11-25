@echo off
REM NAV2BC Migration Factory - demarrage sous Windows (machine de migration)
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.11+ est requis : https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (
  echo Creation de l'environnement Python...
  python -m venv .venv || (pause & exit /b 1)
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip >nul
  pip install -e . || (pause & exit /b 1)
) else (
  call .venv\Scripts\activate.bat
)
start "" http://127.0.0.1:8000
nav2bc serve
