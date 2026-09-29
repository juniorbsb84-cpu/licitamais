"""R52 TDD: fonte caixa, contratos de TI da Caixa assinados a partir de 2026-01-01.

Amostras reais em tests/fixtures/amostras/caixa/ (PNCP, coleta 2026-09-25):
consultas.json (resumo das janelas), t1_pagina1.json (envelope T1),
assinados_2026.json (todos os registros com dataAssinatura maior ou igual
a 2026-01-01), atualizacao_probe.json (/atualizacao, mesmos campos).
Fatos medidos: filtro de data da API e pela publicacao no PNCP, nao pela
assinatura; valorGlobal vem 0.0 na maioria, valorInicial vem preenchido.
"""

from __future__ import annotations

import json
from pathlib import Path

from licitamais.adapters.caixa import (
    ANO_MINIMO,
    ANO_MINIMO_ASSINATURA,
    CNPJ_CAIXA,
    eh_ti,
)

AMOSTRAS = Path(__file__).resolve().parents[0] / "fixtures" / "amostras" / "caixa"


def _registro(
    controle,
    objeto,
    complemento="-",
    assinatura="2026-01-06",
    ni="00000000000100",
    nome="EMPRESA EXEMPLO LTDA",
    tipo="PJ",
    inicial=1000.0,
    global_=0.0,
    compra=None,
    ano=2026,
    seq=1,
    processo="0001/2026",
    tipo_nome="Contrato (termo inicial)",
    uf="DF",
    unidade="CEFOR",
):
    return {
        "numeroControlePNCP": controle,
        "numeroControlePncpCompra": compra,
        "anoContrato": ano,
        "sequencialContrato": seq,
        "niFornecedor": ni,
        "nomeRazaoSocialFornecedor": nome,
        "tipoPessoa": tipo,
        "objetoContrato": objeto,
        "informacaoComplementar": complemento,
        "dataAssinatura": assinatura,
        "dataVigenciaInicio": "2026-01-06",
        "dataVigenciaFim": "2027-01-06",
        "valorInicial": inicial,
        "valorGlobal": global_,
        "tipoContrato": {"nome": tipo_nome},
        "unidadeOrgao": {"ufSigla": uf, "nomeUnidade": unidade},
        "processo": processo,
        "dataPublicacaoPncp": "2026-01-07T22:00:11",
        "dataAtualizacao": "2026-01-08T23:04:20",
    }


# ---------------------------------------------------------------- constantes


def test_constantes_da_fonte():
    assert CNPJ_CAIXA == "00360305000104"
    assert ANO_MINIMO == 2023  # r56: contratos TI desde 2023 (pedido do usuario, 26/09)
    assert ANO_MINIMO_ASSINATURA == "2023-01-01"


def test_campos_sonda_presentes_na_amostra_real():
    from licitamais.adapters.caixa import _CAMPOS_REGISTRO, CAMPOS_SONDA

    assert CAMPOS_SONDA
    envelope = json.loads((AMOSTRAS / "t1_pagina1.json").read_bytes().decode("utf-8"))
    for campo in CAMPOS_SONDA:
        assert campo in envelope, campo
    primeiro = (envelope.get("data") or [])[0]
    for campo in _CAMPOS_REGISTRO:
        assert primeiro.get(campo) not in (None, ""), campo


# ---------------------------------------------------------------- eh_ti


CASOS_REAIS = [
    (
        "PRESTACAO DE SERVICOS DE TECNOLOGIA DA INFORMACAO NECESSARIOS PARA OPERACIONALIZACAO E REALIZACAO DE CONSIGNACAO DE DESCONTOS NOS BENEFICIOS PAGOS PELO INSS REFERENTES A EMPRESTIMO PESSOAL CONSIGNADO E CARTAO DE CREDITO CONSIGNADO. CONVENIO 10605 - INSS",
        "-",
        True,
    ),
    (
        "PRESTACAO DE SERVICO DE FORNECIMENTO DE DADOS CADASTRAIS, COMPORTAMENTAIS E DE RISCO DE PESSOA FISICA E PESSOA JURIDICA, FRANQUIA DE SOLUCOES, SERVICOS SOB DEMANDA, CONSULTORIA ESPECIALIZADA E SOLUCOES EXCLUSIVAS DA SERASA S.A.",
        "-",
        True,
    ),
    (
        "PRESTACAO DE SERVICOS DE SOLUCAO DE AUTHORIZATION SERVER E FIDO SERVER, COM A EMPRESA NETBR DISTRIBUICAO E CONSULTORIA EM INFORMATICA LTDA",
        "-",
        True,
    ),
    (
        "FORNECIMENTO DE SOLUCAO DE CONTROLE DE ACESSO E SEGURANCA ABRANGENDO HARDWARE E SOFTWARE, INSTALACAO, CONFIGURACAO, PROJETO",
        "-",
        True,
    ),
    (
        "SOLICITACAO PARA CONTRATACAO DE SERVICOS TECNICOS ESPECIALIZADOS DE TECNOLOGIA DA INFORMACAO PARA O SISTEMA SIEXC SISTEMA DE COMERCIO EXTERIOR EXCHANGE",
        "-",
        True,
    ),
    (
        "PRESTACAO DE SUPORTE TECNICO E ATUALIZACAO TECNOLOGICA EM 04 EQUIPAMENTOS HSM DINAMO E AQUISICAO DE 01 EQUIPAMENTO HSM DINAMO, POR 36 MESES",
        "-",
        True,
    ),
    (
        "CONTRATACAO DE ATUALIZACAO TECNOLOGICA, AQUISICAO DE NOVAS FEATURES, SUPORTE TECNICO MANUTENCAO E SERVICO ESPECIALIZADO PARA FERRAMENTA DE GESTAO E QUALIDADE",
        "-",
        True,
    ),
    # r58: portal de consignacao (um por convenio) saiu do recorte por decisao do usuario (26/09)
    ("CONTRATACAO DE PORTAL ELETRONICO DE CONSIGNACAO - CONVENIO 07012 - GOVERNO DO ESTADO DA BAHIA", "-", False),
    # r56: contratos reais 2023-2025 que o filtro deixava de fora
    ("PRESTACAO DE SERVICOS ESPECIALIZADOS EM SEGURANCA CIBERNETICA NA CAIXA", "-", True),
    ("PRESTACAO DE SERVICOS ESPECIALIZADOS DE RED TEAM (TESTES DE INTRUSAO AVANCADOS)", "-", True),
    ("PRESTACAO DE SERVICOS DE IMPRESSAO DISTRIBUIDA, INCLUINDO IMPRESSORAS, SOFTWARE DE GERENCIAMENTO", "-", True),
    ("AQUISICAO DE 143.017 MIPS NAS MODALIDADES MES E MO.", "-", True),
    ("FORNECIMENTO DE 2.000 TB DE SOLUCAO DE ARMAZENAMENTO DO TIPO AFA (ALL FLASH ARRAY)", "-", True),
    ("FORNECIMENTO DE SOLUCAO CONVERGENTE DE REDE COMPOSTA POR ROTEADORES CPE", "-", True),
    ("SERVICOS DE LICENCIAMENTO DE USO DOS PROGRAMAS E ENCARGOS CONTINUOS", "-", True),
    ("AQUISICAO DE LICENCAS DO PORTAL DE GOVERNANCA, NA MODALIDADE SAAS SOFTWARE COMO SERVICO", "-", True),
    ("FORNECIMENTO DE LICENCAS DE USO DO SISTEMA QUANTUM", "-", True),
    (
        "CONTRATACAO DIRETA DA EMPRESA RTM - REDE DE TELECOMUNICACOES PARA O MERCADO, PARA A PRESTACAO DE SERVICOS DE TELECOMUNICACOES, PARA FORNECIMENTO DE UM CIRCUITO",
        "-",
        True,
    ),
    (
        "PRESTACAO DE SERVICOS DE CONEXAO DE ACESSO ADSL (CIRCUITO) UTILIZANDO FIBRA OTICA, RADIO OU VSAT DE BAIXA ORBITA DESTINADO A ALENQUER PA",
        "-",
        True,
    ),
    (
        "EVENTO EXTERNO DE T E D - INSCRICAO DE DOIS DIRIGENTES DA SUBSIDIARIA CAIXA LOTERIAS S A NO CURSO CAPACITACAO DO ENCARREGADO PELO TRATAMENTO DE DADOS PESSOAIS (LGPD) CURSO SERPRO P01S (EAD AO VIVO)",
        "-",
        False,
    ),
    (
        "SOLICITAMOS O APOIO DESTA CENTRALIZADORA PARA SUPORTE NA CONTRATACAO ADESAO AO CORPORATE ENGAGEMENT PROGRAM DA SCIENCE BASED TARGETS NETWORK (SBTN), COM DURACAO DE 12 MESES",
        "-",
        False,
    ),
    (
        "SERVICO COMUM DE ENGENHARIA (SCE) PARA FORNECIMENTO E INSTALACAO DE SISTEMA DE AR CONDICIONADO PARA AS UNIDADES DA CAIXA",
        "-",
        False,
    ),
    (
        "FORNECIMENTO E INSTALACAO DE SINALIZACAO INTERNA, EXTERNA, DE ACESSIBILIDADE E DE CARENAGEM PARA UNIDADES CAIXA EXISTENTES E NOVAS",
        "-",
        False,
    ),
    (
        "SERVICOS COMUNS DE ENGENHARIA EM IMOVEIS DE USO DA CAIXA NA REGIAO DE ABRANGENCIA DA SR FLORIANOPOLIS",
        "-",
        False,
    ),
    (
        "PRESTACAO DE SERVICOS TERCEIRIZADOS DE ENGENHARIA E ARQUITETURA, NECESSARIOS A ADMINISTRACAO, CONSERVACAO E MANUTENCAO DE IMOVEIS DE USO DA CAIXA",
        "-",
        False,
    ),
    ("CONTRATACAO DE EMPRESA PARA PRESTACAO DO SERVICO DE LIMPEZA GERAL NO IMOVEL CAIXA", "-", False),
    (
        "PRESTACAO DE SERVICOS DE LIMPEZA, JARDINAGEM E CONTROLE DE PRAGAS, INCLUINDO TODOS OS MATERIAIS E INSUMOS NECESSARIOS",
        "-",
        False,
    ),
    (
        "PRESTACAO DE SERVICOS COMUNS DE TRANSPORTE, TRATAMENTO E CUSTODIA DE VALORES A UNIDADES CAIXA, UNIDADES LOTERICAS (UL)",
        "-",
        False,
    ),
    (
        "FORNECIMENTO E MONTAGEM DE MOBILIARIO ESTOFADO PARA AS UNIDADES DA CAIXA LOCALIZADAS NA REGIAO SUDESTE DO PAIS",
        "-",
        False,
    ),
    (
        "CREDENCIAMENTO DE IMOBILIARIAS PARA PRESTACAO DE SERVICOS DE INTERMEDIACAO E ASSESSORAMENTO DE VENDA DE IMOVEIS NAO DE USO DE PROPRIEDADE DA CAIXA",
        "-",
        False,
    ),
    ("CONTRATACAO DE 01 INSCRICAO PARA O CURSO PLANEJAMENTO TRIBUTARIO COM CARGA HORARIA", "-", False),
    (
        "SERVICOS DE MANUTENCAO MINIMA PERIODICA E MANUTENCAO CORRETIVA NAS CONTROLADORAS BIOMETRICAS, CFTV, PSDM, NA SOLUCAO RESTRITORA DE VISIBILIDADE",
        "-",
        False,
    ),
]


def test_eh_ti_casos_reais_da_api():
    assert len(CASOS_REAIS) >= 15
    for objeto, complemento, esperado in CASOS_REAIS:
        assert eh_ti(objeto, complemento) is esperado, objeto[:80]


def test_eh_ti_aceita_complemento_none_e_texto_vazio():
    assert eh_ti("PRESTACAO DE SERVICOS DE TECNOLOGIA DA INFORMACAO", None) is True
    assert eh_ti("", None) is False
    assert eh_ti(None, None) is False


def test_eh_ti_fornecedor_tipico_nao_basta():
    assert eh_ti("CONTRATACAO DE 01 INSCRICAO PARA CURSO DE GESTAO", "-") is False
    assert eh_ti("FORNECIMENTO E MONTAGEM DE MOBILIARIO", "-") is False


def test_eh_ti_termo_ti_claro_vence_exclusao_de_obra():
    assert (
        eh_ti(
            "SERVICOS COMUNS DE ENGENHARIA PARA IMPLANTACAO DE DATA CENTER COM INFRAESTRUTURA DE REDE E NOBREAKS", "-"
        )
        is True
    )


def test_eh_ti_terceirizacao_de_ti_listada():
    assert eh_ti("CONTRATACAO DE FABRICA DE SOFTWARE PARA DESENVOLVIMENTO E SUSTENTACAO DE SISTEMAS", "-") is True
    assert (
        eh_ti(
            "ALOCACAO DE PROFISSIONAIS DE TI PARA SERVICE DESK E OPERACAO DE INFRAESTRUTURA DE REDE E DATA CENTER", "-"
        )
        is True
    )
    assert (
        eh_ti(
            "PRESTACAO DE SERVICOS DE CONSULTORIA DE TI E SUPORTE TECNICO DE TI COM SUBSCRICAO DE SOFTWARE EM NUVEM",
            "-",
        )
        is True
    )


# ---------------------------------------------------------------- plano


def test_plano_primeira_carga_emite_janelas_trimestrais_2026():
    from licitamais.adapters.caixa import CaixaAdapter
    from licitamais.types import PlanContext

    pedidos = list(CaixaAdapter().plan(PlanContext({}, frozenset(), 0, 1)))
    assert len(pedidos) == 16  # r56: trimestres de 2023 a 2026
    assert all(p.method == "GET" for p in pedidos)
    assert all(p.phase == "discover" for p in pedidos)
    assert all(p.endpoint == "/api/consulta/v1/contratos" for p in pedidos)
    janelas = sorted((p.params["dataInicial"], p.params["dataFinal"]) for p in pedidos)
    assert janelas[0] == ("20230101", "20230331")
    assert janelas[-1] == ("20261001", "20261231")
    assert all(p.params["cnpjOrgao"] == "00360305000104" for p in pedidos)
    assert all(p.params["pagina"] == "1" for p in pedidos)
    assert all(p.params["tamanhoPagina"] == "500" for p in pedidos)


def test_plano_coleta_incremental_usa_atualizacao_ultimos_10_dias():
    from licitamais.adapters.caixa import CaixaAdapter
    from licitamais.types import PlanContext

    ctx = PlanContext({"caixa_modo": "incremental"}, frozenset(), 0, 1)
    (pedido,) = list(CaixaAdapter().plan(ctx))
    assert pedido.endpoint == "/api/consulta/v1/contratos/atualizacao"
    assert pedido.phase == "discover"
    assert pedido.params["cnpjOrgao"] == "00360305000104"

    from datetime import UTC, datetime, timedelta

    hoje = datetime.now(UTC).date()
    esperado_ini = (hoje - timedelta(days=10)).strftime("%Y%m%d")
    assert pedido.params["dataInicial"] == esperado_ini
    assert pedido.params["dataFinal"] == hoje.strftime("%Y%m%d")


# ---------------------------------------------------------------- parse


def _pagina(adapter, pedido, corpo: bytes):
    from licitamais.types import FetchedPage

    return FetchedPage(
        request=pedido,
        status=200,
        headers={"content-type": "application/json; charset=utf-8"},
        body=corpo,
        fetched_at="2026-09-25T12:00:00Z",
        duration_ms=1,
    )


def _pedido_pagina(inicial="20260101", final="20260331", pagina="1"):
    from licitamais.types import FetchRequest

    return FetchRequest(
        endpoint="/api/consulta/v1/contratos",
        method="GET",
        params={
            "dataInicial": inicial,
            "dataFinal": final,
            "cnpjOrgao": "00360305000104",
            "pagina": pagina,
            "tamanhoPagina": "500",
        },
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="contract",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def test_parse_pagina_real_so_grava_ti_assinado_em_2026_com_pai_e_valor():
    import sqlite3

    from licitamais.adapters.caixa import CaixaAdapter
    from licitamais.loader import load_batch
    from licitamais.runner import open_source_run
    from licitamais.schema import init_schema

    adapter = CaixaAdapter()
    envelope = json.loads((AMOSTRAS / "t1_pagina1.json").read_bytes().decode("utf-8"))
    pagina1 = {
        "data": envelope["data"],
        "totalRegistros": envelope["totalRegistros"],
        "totalPaginas": envelope["totalPaginas"],
    }
    resultado = adapter.parse(_pagina(adapter, _pedido_pagina(), json.dumps(pagina1).encode("utf-8")))
    assert resultado.fatal is None
    # r56: recorte desde 2023; a pagina real traz contratos TI de 2023-2025
    assert all(c.attrs["signed_at_source"] >= "2023-01-01" for c in resultado.batch.contracts)
    assert resultado.signals["row_count_declared"] == envelope["totalRegistros"]

    assinados = json.loads((AMOSTRAS / "assinados_2026.json").read_bytes().decode("utf-8"))
    pagina_ti = {"data": assinados, "totalRegistros": len(assinados), "totalPaginas": 1}
    resultado = adapter.parse(_pagina(adapter, _pedido_pagina(pagina="2"), json.dumps(pagina_ti).encode("utf-8")))
    assert resultado.fatal is None
    contratos = {c.source_native_id: c for c in resultado.batch.contracts}
    assert contratos, "amostra assinados_2026.json deveria render ao menos 1 contrato TI"
    for contrato in contratos.values():
        attrs = contrato.attrs
        assert attrs["signed_at_source"] >= "2026-01-01"
        assert attrs["process_native_id"]
        assert attrs["value_raw"] is not None
    pais = {p.source_native_id for p in resultado.batch.processes}
    assert {c.attrs["process_native_id"] for c in contratos.values()} <= pais

    con = sqlite3.connect(":memory:")
    con.row_factory = con.row_factory = __import__("sqlite3").Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    source_id = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual)"
        " VALUES ('caixa', 'api_json', 'https://pncp.gov.br', '0.1.0')"
    ).lastrowid
    con.commit()
    run_id = open_source_run(con, source_id, "manual", "0.1.0")
    carga = load_batch(con, run_id, source_id, resultado.batch)
    con.commit()
    assert carga.quarantined == 0
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == len(contratos)
    assert con.execute("SELECT COUNT(*) FROM parse_quarantine").fetchone()[0] == 0
    con.close()


def test_parse_valor_usa_inicial_e_global_so_se_positivo():
    from licitamais.adapters.caixa import _parse_lista

    so_global = _registro(
        "00360305000104-2-000001/2026", "PRESTACAO DE SERVICOS DE TECNOLOGIA DA INFORMACAO", inicial=0.0, global_=2500.5
    )
    so_inicial = _registro(
        "00360305000104-2-000002/2026",
        "AQUISICAO DE LICENCAS DE USO DO SISTEMA QUANTUM",
        inicial=127758.36,
        global_=0.0,
    )
    resultado = _parse_lista([so_global, so_inicial], "20260101")
    por_id = {c.source_native_id: c for c in resultado.batch.contracts}
    assert por_id["00360305000104-2-000001/2026"].attrs["value_raw"] == 2500.5
    assert por_id["00360305000104-2-000002/2026"].attrs["value_raw"] == 127758.36


def test_parse_pula_ano_anterior_e_nao_ti_sem_quarentena():
    from licitamais.adapters.caixa import _parse_lista

    registros = [
        _registro(
            "00360305000104-2-000001/2026", "PRESTACAO DE SERVICOS DE TECNOLOGIA DA INFORMACAO", assinatura="2022-12-31"
        ),
        _registro("00360305000104-2-000002/2026", "FORNECIMENTO E MONTAGEM DE MOBILIARIO ESTOFADO"),
    ]
    resultado = _parse_lista(registros, "20260101")
    assert resultado.fatal is None
    assert resultado.batch.contracts == ()
    assert resultado.batch.processes == ()
    assert resultado.quarantine == ()
    assert resultado.signals["filtrados"] == 2


def test_parse_id_processo_usa_compra_quando_ha_e_contrato_quando_falta():
    from licitamais.adapters.caixa import _parse_lista

    com_compra = _registro(
        "00360305000104-2-000001/2026",
        "PRESTACAO DE SERVICOS DE TECNOLOGIA DA INFORMACAO",
        compra="00360305000104-1-000001/2026",
    )
    sem_compra = _registro(
        "00360305000104-2-000002/2026", "AQUISICAO DE LICENCAS DE USO DO SISTEMA QUANTUM", compra=None
    )
    resultado = _parse_lista([com_compra, sem_compra], "20260101")
    por_processo = {p.source_native_id: p for p in resultado.batch.processes}
    assert "caixa:00360305000104-1-000001/2026" in por_processo
    assert "caixa:contrato:00360305000104-2-000002/2026" in por_processo
    por_contrato = {c.source_native_id: c for c in resultado.batch.contracts}
    assert (
        por_contrato["00360305000104-2-000001/2026"].attrs["process_native_id"] == "caixa:00360305000104-1-000001/2026"
    )
    assert (
        por_contrato["00360305000104-2-000002/2026"].attrs["process_native_id"]
        == "caixa:contrato:00360305000104-2-000002/2026"
    )


def test_parse_processo_pai_traz_campos_do_modelo():
    from licitamais.adapters.caixa import _parse_lista

    (unico,) = _parse_lista(
        [
            _registro(
                "00360305000104-2-000002/2026",
                "PRESTACAO DE SERVICO DE FORNECIMENTO DE DADOS CADASTRAIS",
                compra="00360305000104-1-000538/2025",
                ni="33000167000101",
                nome="SERASA S/A",
                inicial=106000000.0,
                processo="0538/2025",
                uf="SP",
                unidade="CEFOR SP",
            )
        ],
        "20260101",
    ).batch.processes
    assert unico.attrs["number"] == "00360305000104-1-000538/2025"
    assert unico.attrs["object"] == "PRESTACAO DE SERVICO DE FORNECIMENTO DE DADOS CADASTRAIS"
    assert unico.attrs["org_native_id"] == "CAIXA"
    assert unico.attrs["modality_raw"] == "Contrato (termo inicial)"
    assert unico.attrs["year"] == 2026
    assert unico.attrs["cnpj_orgao"] == "00360305000104"
    assert unico.attrs["ano_contrato"] == 2026
    assert unico.attrs["sequencial_contrato"] == 1
    assert unico.attrs["uf"] == "SP"
    assert unico.attrs["unidade"] == "CEFOR SP"


def test_parse_contrato_pf_nao_tem_cnpj_e_traz_vigencia():
    from licitamais.adapters.caixa import _parse_lista

    registros = [
        _registro(
            "00360305000104-2-000001/2026",
            "PRESTACAO DE SERVICOS DE TECNOLOGIA DA INFORMACAO",
            ni="12345678901",
            nome="FULANO DE TAL",
            tipo="PF",
        )
    ]
    (contrato,) = _parse_lista(registros, "20260101").batch.contracts
    assert contrato.attrs["supplier_cnpj"] is None
    assert contrato.attrs["supplier_name_raw"] == "FULANO DE TAL"
    assert contrato.attrs["signed_at_source"] == "2026-01-06"
    assert contrato.attrs["vigency_start"] == "2026-01-06"
    assert contrato.attrs["vigency_end"] == "2027-01-06"


def test_parse_paginacao_emite_proxima_quando_ha_mais():
    from licitamais.adapters.caixa import CaixaAdapter

    adapter = CaixaAdapter()
    pagina = {"data": [], "totalRegistros": 1000, "totalPaginas": 2}
    resultado = adapter.parse(_pagina(adapter, _pedido_pagina(pagina="1"), json.dumps(pagina).encode("utf-8")))
    assert resultado.fatal is None
    assert len(resultado.next) == 1
    proximo = resultado.next[0]
    assert proximo.params["pagina"] == "2"
    assert proximo.params["dataInicial"] == "20260101"
    assert resultado.batch.processes == ()  # sem processo de controle falso
    assert resultado.signals["pagina_filtrada"] is True

    ultima = {"data": [], "totalRegistros": 1000, "totalPaginas": 2}
    resultado = adapter.parse(_pagina(adapter, _pedido_pagina(pagina="2"), json.dumps(ultima).encode("utf-8")))
    assert resultado.fatal is None
    assert resultado.next == ()


def test_parse_envelope_invalido_e_fatal():
    from licitamais.adapters.caixa import CaixaAdapter

    adapter = CaixaAdapter()
    resultado = adapter.parse(_pagina(adapter, _pedido_pagina(), b"nao e json"))
    assert resultado.fatal is not None
    resultado = adapter.parse(_pagina(adapter, _pedido_pagina(), b'{"totalRegistros": 1}'))
    assert resultado.fatal is not None


def test_fetch_monta_url_com_base_e_parametros():
    from licitamais.adapters.caixa import CaixaAdapter
    from licitamais.types import SourceConfig

    adapter = CaixaAdapter()
    sessao = adapter.open(SourceConfig(source_code="caixa", base_url="https://pncp.gov.br"))
    try:
        pedido = _pedido_pagina()
        chamadas = {}

        class _Resposta:
            status_code = 200
            headers = {"content-type": "application/json"}
            content = b'{"data": [], "totalRegistros": 0, "totalPaginas": 1}'

        def _fake(method, url, params=None, headers=None, timeout=None):
            chamadas["method"] = method
            chamadas["url"] = url
            chamadas["params"] = params
            return _Resposta()

        sessao.dados["http"].request = _fake
        pagina = adapter.fetch(sessao, pedido)
        assert chamadas["url"] == "https://pncp.gov.br/api/consulta/v1/contratos"
        assert chamadas["params"]["cnpjOrgao"] == "00360305000104"
        assert pagina.status == 200
    finally:
        adapter.close(sessao)
