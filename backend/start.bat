@echo off
REM Start the CUS AI backend.
REM Prerequisites: Ollama running locally with llama3 + nomic-embed-text pulled.
REM   ollama pull llama3
REM   ollama pull nomic-embed-text
REM   ollama serve   (if not already running)

cd /d "%~dp0"
python run.py
