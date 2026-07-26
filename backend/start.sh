#!/usr/bin/env bash
# Start the CUS AI backend.
# Prerequisites: Ollama running with llama3 + nomic-embed-text pulled.
set -e
cd "$(dirname "$0")"
python run.py
