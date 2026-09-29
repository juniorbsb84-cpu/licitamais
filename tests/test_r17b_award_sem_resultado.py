"""R17b: award sem nenhuma referencia de resultado vai para quarentena e nao duplica."""

from licitamais.loader import load_batch
from licitamais.types import AwardRecord
from tests.test_r17_loader_orfaos import con, lote  # noqa: F401  (fixture)


def test_award_sem_referencia_de_resultado_quarentena(con):  # noqa: F811
    award = AwardRecord(
        source_native_id="award-sem-ref",
        attrs={"supplier_name_raw": "Fornecedor", "amount_cents": 1000},
    )
    r1 = load_batch(con, 1, 1, lote(awards=(award,)))
    r2 = load_batch(con, 2, 1, lote(awards=(award,)))

    assert r1.quarantined == 1 and r2.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 0
