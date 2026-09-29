"""r57: BB e BBTS usam o mesmo adaptador PNCP da Caixa, cada um com seu CNPJ."""

from licitamais.adapters.caixa import BBAdapter, BBTSAdapter, CaixaAdapter, _parse_lista
from licitamais.types import PlanContext


def _registro(controle, cnpj, razao, compra=None):
    return {
        "numeroControlePNCP": controle,
        "numeroControlePncpCompra": compra,
        "orgaoEntidade": {"cnpj": cnpj, "razaoSocial": razao},
        "anoContrato": 2025,
        "sequencialContrato": 53,
        "unidadeOrgao": {"ufSigla": "DF", "nomeUnidade": "DIRECAO GERAL"},
        "dataPublicacaoPncp": "2025-07-10T10:00:00",
        "valorInicial": 34679556.0,
        "valorGlobal": 0.0,
        "dataAssinatura": "2025-07-09",
        "dataVigenciaInicio": "2025-07-09",
        "dataVigenciaFim": "2030-07-09",
        "tipoContrato": {"id": 1, "nome": "Contrato (termo inicial)"},
        "niFornecedor": "59456277000176",
        "nomeRazaoSocialFornecedor": "ORACLE DO BRASIL SISTEMAS LTDA",
        "tipoPessoa": "PJ",
        "objetoContrato": "PRESTACAO DE SERVICOS DA SOLUCAO ORACLE CLOUD INFRASTRUCTURE (OCI) - COMPUTACAO EM NUVEM",
        "informacaoComplementar": "-",
    }


def test_plano_de_cada_orgao_usa_o_proprio_cnpj():
    for adapter, cnpj in (
        (CaixaAdapter(), "00360305000104"),
        (BBAdapter(), "00000000000191"),
        (BBTSAdapter(), "42318949001318"),
    ):
        pedidos = list(adapter.plan(PlanContext({}, frozenset(), 0, 1)))
        assert pedidos and all(p.params["cnpjOrgao"] == cnpj for p in pedidos)
    assert (BBAdapter.source_code, BBTSAdapter.source_code) == ("bb", "bbts")


def test_registro_do_bbts_vira_processo_e_contrato_do_bbts():
    reg = _registro("42318949001318-2-000053/2025", "42318949001318", "BB TECNOLOGIA E SERVICOS S.A.")
    resultado = _parse_lista([reg], "20250701-20250930")
    (processo,) = resultado.batch.processes
    (contrato,) = resultado.batch.contracts
    assert processo.source_native_id == "bbts:contrato:42318949001318-2-000053/2025"
    assert processo.attrs["org_native_id"] == "BBTS"
    assert processo.attrs["cnpj_orgao"] == "42318949001318"
    assert contrato.attrs["org_native_id"] == "BBTS"


CASOS_BB = [
    # objetos reais BB/BBTS (PNCP, classificados manualmente)
    ("Remanescente CTR 202474210299 - LE 2023/02776 Conectividade Rede IP", True),
    (
        "Contratação do serviço de conectividade IP para acesso à Rede Internet Mundial para as dependências do BANCO DO BRASIL",
        True,
    ),
    (
        "Serviços de comunicação de dados entre as dependências do CONTRATANTE, Pontos Remotos, com os pontos centrais de processamento de dados",
        True,
    ),
    (
        "Contratação de empresa especializada na prestação de serviços de Outsourcing de impressão, com recursos de reprografia e digitalização",
        True,
    ),
    ("prestação de serviços de Suporte Técnico & Subscrição de Licenças de Software (S&S)", True),
    ("Prestação de serviços da solução Oracle Cloud Infrastructure (OCI) do fornecedor Oracle do Brasil Ltda", True),
    (
        "Contratação de Solução Integrada de Software de Gestão Empresarial (ERP), por meio do modelo RISE with SAP",
        True,
    ),
    ("Serviços de Validação e Emissão de Certificados Digitais do tipo A3 em nuvem, padrão ICP-Brasil.", True),
    ("Aquisição de 90 Tablets com sistema operacional Android.", True),
    ("Registro dos preços, pela BBTS, para aquisição de smartphones desbloqueados", True),
    (
        "Contratação direta de serviço de fornecimento de base de dados para consulta a Nota Fiscal Eletrônica NFE via API",
        True,
    ),
    ("Aquisição de solução de segurança de Network Packet Broker, incluindo licenciamento, instalação", True),
    (
        "Prestação de serviços especializados em transmissão (MT Mobile Terminator) e recepção (MO Mobile Originator) de mensagens curtas (SMS)",
        True,
    ),
    ("Aquisição de pacotes de conexão e acesso à internet via satélite operando em Baixa Órbita (LEO)", True),
    ("Aquisição de Solução de Governança e Administração de Identidade (IGA)", True),
    ("Aquisição de Disco Rígido (Hard Disc ou HD) Para NVR.", True),
    ("Aquisição de módulos, partes e peças para sistemas de controle de acesso (Catracas)", False),
    (
        "Contratação de empresa especializada no gerenciamento de sistema informatizado e integrado para o abastecimento contínuo de combustíveis automotivos",
        False,
    ),
    (
        "Aquisição, pelo CONTRATANTE, de Compressor para equipamento de ar-condicionado em operação no Data Center ICI II",
        False,
    ),
    (
        "Serviço de construção civil de tratamento de infiltrações em juntas e lajes por meio de impermeabilização, no Data Center",
        False,
    ),
    # Caixa, coleta r57: falsos positivos reais
    (
        "PRESTAÇÃO DOS SERVIÇOS DE LOCAÇÃO DE SISTEMA DE ALARME MONITORADO, INCLUÍDOS INSTALAÇÃO, com software de gestão",
        False,
    ),
    (
        "EMPRESA PARA PRESTAÇÃO SERVIÇO DE ENSINO DE IDIOMAS POR MEIO DE PLATAFORMA DIGITAL EDUCACIONAL, com software",
        False,
    ),
    ("AQUISIÇÃO E SUBSCRIÇÃO DE LICENÇAS DE SOFTWARES MICROSOFT, INCLUINDO APOIO TÉCNICO", True),
    ("PRESTAÇÃO DE SERVIÇO SERPRO MULTICLOUD, COM ABRANGÊNCIA NACIONAL, PELO PERÍODO DE 60 MESES.", True),
]


def test_eh_ti_vocabulario_bb_bbts():
    from licitamais.adapters.caixa import eh_ti

    erros = [obj[:70] for obj, esperado in CASOS_BB if eh_ti(obj, None) is not esperado]
    assert erros == []


def test_caixa_mantem_ids_antigos():
    reg = _registro(
        "00360305000104-2-000001/2026",
        "00360305000104",
        "CAIXA ECONOMICA FEDERAL",
        compra="00360305000104-1-000001/2026",
    )
    (processo,) = _parse_lista([reg], "x").batch.processes
    assert processo.source_native_id == "caixa:00360305000104-1-000001/2026"
    assert processo.attrs["org_native_id"] == "CAIXA"
