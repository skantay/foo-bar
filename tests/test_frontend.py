"""Фронтенд без сборки: каждый модуль должен парситься, а импорты — указывать на существующие файлы."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = sorted((Path(__file__).resolve().parent.parent / "frontend" / "js").rglob("*.js"))


@pytest.mark.skipif(not shutil.which("node"), reason="нужен Node.js")
@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_js_syntax(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_relative_imports_exist(path):
    for spec in re.findall(r"""^\s*import\s+(?:[^'"]+\s+from\s+)?["'](\.[^"']+)["']""", path.read_text("utf-8"), re.M):
        assert (path.parent / spec).resolve().exists(), f"{path.name}: нет модуля {spec}"
