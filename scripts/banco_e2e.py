"""Banco pequeno para o e2e: reutiliza a semeadura dos testes da API."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

from api.conftest import semear  # noqa: E402
from licitamais.schema import init_schema  # noqa: E402


def main(destino: str, abertas: int = 30) -> None:
    caminho = Path(destino)
    if caminho.exists():
        caminho.unlink()
    con = sqlite3.connect(str(caminho))
    init_schema(con)
    semear(con)
    base = con.execute("SELECT COALESCE(MAX(id), 0) FROM process").fetchone()[0]
    for i in range(1, abertas + 1):
        pid = base + i
        con.execute(
            "INSERT INTO process (id, source_id, source_native_id, org_id, number, object, modality_raw,"
            " opening_at_source, phase_current_label, phase_current_group, record_hash,"
            " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
            " VALUES (?, 2, ?, 2, ?, ?, 'Pregão eletrônico', '2099-12-01', 'Edital Aberto', 'aberta', 'h', 1, 1, 1)",
            (pid, f"e2e-{i}", f"{100 + i:03d}/2026", f"Objeto de teste e2e número {i} para filtro Aberta"),
        )
    con.commit()
    con.close()
    print(f"e2e: {abertas} abertas em {caminho}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tmp_e2e.db")
