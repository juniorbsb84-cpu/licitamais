"""Grava frontend/openapi.json a partir da app (banco em memoria)."""

import json
import pathlib
import tempfile

from licitamais.api import create_app

with tempfile.TemporaryDirectory() as d:
    spec = create_app(str(pathlib.Path(d) / "vazio.db")).openapi()
destino = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"
destino.parent.mkdir(exist_ok=True)
destino.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
print(destino)
