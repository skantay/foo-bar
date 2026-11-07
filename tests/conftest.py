"""Общие фикстуры: приложение на временной базе и мок-оценщике — без сети, ключей и реальной модели."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

# Переменные задаются до импорта app: он читает .env через setdefault, поэтому заданное здесь не перезапишется.
os.environ["CQA_BACKEND"] = "mock"
os.environ["CQA_SAMPLES"] = "3"
os.environ["CQA_PII_MASK"] = "1"
os.environ["CQA_SOURCE_URL"] = "https://example.invalid/api?sig=test"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import app as app_module  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def dialogs_payload() -> dict:
    return json.loads((FIXTURES / "dialogs.json").read_text("utf-8"))


@pytest.fixture
def default_checklist() -> dict:
    return json.loads((ROOT / "backend" / "checklist_default.json").read_text("utf-8"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Чистая база в tmp_path; кеш DEV-набора не подхватывается."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cqa.db")
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    app_module.JOBS.clear()
    app_module._cl_cache.clear()
    with TestClient(app_module.app) as c:
        yield c
    if db._conn is not None:
        db._conn.close()


@pytest.fixture
def loaded(client):
    """Клиент с загруженными тестовыми диалогами."""
    r = client.post("/api/dialogs/upload?dataset=TEST",
                    files={"file": ("dialogs.json", (FIXTURES / "dialogs.json").read_bytes(), "application/json")})
    assert r.status_code == 200, r.text
    return client


def wait_job(client, job_id: str, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] == "done":
            return j
        time.sleep(0.05)
    raise AssertionError(f"задача {job_id} не завершилась за {timeout} с")


@pytest.fixture
def evaluated(loaded):
    """Клиент, в котором все тестовые диалоги оценены активным чек-листом."""
    job = loaded.post("/api/evaluations/run", json={}).json()
    wait_job(loaded, job["id"])
    return loaded


def latest_eval(client, dialog_id: str) -> dict:
    brief = next(e for e in client.get(f"/api/dialogs/{dialog_id}").json()["evaluations"] if e["status"] == "done")
    return client.get(f"/api/evaluations/{brief['id']}").json()
