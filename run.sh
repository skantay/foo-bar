#!/usr/bin/env sh
# Запуск CQA: API + интерфейс на http://127.0.0.1:${PORT:-8000}
cd "$(dirname "$0")/backend" && exec ../.venv/bin/uvicorn app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
