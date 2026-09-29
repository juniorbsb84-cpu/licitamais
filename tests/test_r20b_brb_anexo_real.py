"""R20b: formato REAL de anexo do BRB e processo antigo sem realizacao (coleta 2026-09-23)."""

import json

from licitamais.adapters.brb import BRBAdapter
from tests.test_r20_brb_api_real import _pagina, _registros_fixture

ANEXO_REAL = {
    "id": "e51c525430b533441f392f680fcd07bb",
    "arquivo": "Edital Leilão 029.2026 FUNDEFE.pdf",
    "file": None,
    "descricao": None,
    "cadastro": "2026-09-21T10:33:46",
}


def _parse(registros):
    return BRBAdapter().parse(_pagina(json.dumps(registros, ensure_ascii=False).encode("utf-8")))


def test_anexo_real_sem_url_vira_attachment_sem_quarentena():
    reg = dict(_registros_fixture()[0], anexos=[ANEXO_REAL])
    r = _parse([reg])
    assert not r.quarantine, [q.reason for q in r.quarantine]
    assert len(r.batch.attachments) == 1
    att = r.batch.attachments[0]
    assert att.attrs["filename"] == ANEXO_REAL["arquivo"]
    assert ANEXO_REAL["id"] in att.source_native_id


def test_processo_antigo_sem_realizacao_nao_vai_para_quarentena():
    reg = dict(_registros_fixture()[0])
    reg["realizacao"] = None
    r = _parse([reg])
    assert len(r.batch.processes) == 1
    assert not r.quarantine, [q.reason for q in r.quarantine]
