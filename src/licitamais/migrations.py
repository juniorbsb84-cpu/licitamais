import pathlib
import re
import sqlite3

_PADRAO_MIGRACAO = re.compile(r"^(\d+)_.*\.sql$")
_PADRAO_PREFIXO_NUMERICO = re.compile(r"^\d+")


def current_version(con: sqlite3.Connection) -> int:
    linha = con.execute("PRAGMA user_version").fetchone()
    return int(linha[0]) if linha else 0


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    linha = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return linha is not None


def apply_migrations(con: sqlite3.Connection, migrations_dir: pathlib.Path) -> int:
    migracoes = _listar_migracoes(pathlib.Path(migrations_dir))
    versao_atual = current_version(con)
    pendentes = [(versao, caminho) for versao, caminho in migracoes if versao > versao_atual]

    for versao, caminho in pendentes:
        _aplicar_migracao(con, versao, caminho)

    return len(pendentes)


def _listar_migracoes(
    diretorio: pathlib.Path,
) -> list[tuple[int, pathlib.Path]]:
    if not diretorio.is_dir():
        raise FileNotFoundError(f"diretorio de migracoes inexistente: {diretorio}")

    arquivos: list[pathlib.Path] = []
    for caminho in diretorio.iterdir():
        if not caminho.is_file():
            continue
        if caminho.suffix == ".sql":
            arquivos.append(caminho)
            continue
        if _PADRAO_PREFIXO_NUMERICO.match(caminho.name):
            raise ValueError(f"arquivo de migracao sem extensao .sql: {caminho.name}")

    if not arquivos:
        raise ValueError(f"nenhuma migracao encontrada em: {diretorio}")

    migracoes: list[tuple[int, pathlib.Path]] = []
    for caminho in arquivos:
        encontrado = _PADRAO_MIGRACAO.fullmatch(caminho.name)
        if encontrado is None:
            raise ValueError(f"nome de migracao invalido: {caminho.name}")
        migracoes.append((int(encontrado.group(1)), caminho))

    migracoes.sort(key=lambda migracao: migracao[0])
    versoes = [versao for versao, _ in migracoes]

    if len(set(versoes)) != len(versoes):
        raise ValueError("versao de migracao duplicada")

    esperadas = list(range(1, len(versoes) + 1))
    if versoes != esperadas:
        raise ValueError(f"lacuna de versao: encontradas {versoes}, esperadas {esperadas}")

    return migracoes


def _aplicar_migracao(
    con: sqlite3.Connection,
    versao: int,
    caminho: pathlib.Path,
) -> None:
    sql = caminho.read_text(encoding="utf-8")

    con.execute("BEGIN")
    try:
        for sentenca in _separar_sentencas(sql):
            con.execute(sentenca)

        con.execute(
            "INSERT INTO schema_migration (version) VALUES (?)",
            (versao,),
        )
        con.execute(f"PRAGMA user_version = {versao}")
        con.execute("COMMIT")
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


def _separar_sentencas(sql: str) -> list[str]:
    sentencas: list[str] = []
    atual: list[str] = []

    for caractere in sql:
        atual.append(caractere)
        candidato = "".join(atual)

        if caractere == ";" and sqlite3.complete_statement(candidato):
            sentenca = candidato.strip()
            if sentenca:
                sentencas.append(sentenca)
            atual = []

    resto = "".join(atual).strip()
    if resto:
        sentencas.append(resto)

    return sentencas
