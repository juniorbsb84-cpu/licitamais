"""R18b: adaptador sem campos obrigatorios declarados nao ganha sonda inventada."""

import json
import types

import pytest

from licitamais.fontes import _campos_obrigatorios


def test_adaptador_sem_campos_declarados_falha_alto():
    with pytest.raises(ValueError):
        _campos_obrigatorios(types.ModuleType("adaptador_sem_campos"))


def test_adaptador_com_campos_vazios_falha_alto():
    modulo = types.ModuleType("adaptador_vazio")
    modulo._CAMPOS_OBRIGATORIOS = ()
    with pytest.raises(ValueError):
        _campos_obrigatorios(modulo)


def test_sonda_do_sistema_industria_usa_campos_do_payload_real():
    import sqlite3

    from licitamais.fontes import registrar_fontes
    from licitamais.schema import init_schema

    con = sqlite3.connect(":memory:")
    init_schema(con)
    registrar_fontes(con)
    campos = con.execute(
        "SELECT required_fields FROM source_probe p JOIN source s ON s.id = p.source_id"
        " WHERE s.code = 'sistema_industria'"
    ).fetchone()[0]
    # descoberta real devolve {Data:[{Id}], RowsCount...}: so Id e garantido
    assert json.loads(campos) == ["id"]
