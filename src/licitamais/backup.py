import pathlib
import shutil
import sqlite3
from datetime import date


def vacuum_backup(db_path: pathlib.Path, backup_dir: pathlib.Path, day: date) -> pathlib.Path:
    diretorio = pathlib.Path(backup_dir)
    diretorio.mkdir(parents=True, exist_ok=True)
    saida = diretorio / f"backup-{day.strftime('%Y%m%d')}.db"
    con = sqlite3.connect(str(db_path))
    try:
        path_str = str(saida).replace("'", "''")
        con.execute(f"VACUUM INTO '{path_str}'")
    finally:
        con.close()
    return saida


def rotate_backups(backup_dir: pathlib.Path, keep_days: int = 7) -> int:
    try:
        manter = int(keep_days)
    except Exception:
        manter = 7
    if manter < 0:
        manter = 0
    diretorio = pathlib.Path(backup_dir)
    if not diretorio.is_dir():
        return 0
    arquivos = sorted(diretorio.glob("backup-*.db"), reverse=True)
    apagados = 0
    for arquivo in arquivos[manter:]:
        try:
            arquivo.unlink()
            apagados += 1
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return apagados


def copy_external(backup_path: pathlib.Path, dest: str) -> bool:
    if not dest:
        return False
    try:
        origem = pathlib.Path(str(backup_path))
        if not origem.is_file():
            return False
        destino = pathlib.Path(str(dest))
        if destino.is_dir():
            destino = destino / origem.name
        pai = destino.parent
        if str(pai) and str(pai) not in (".", "") and not pai.exists():
            pai.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(origem), str(destino))
        return True
    except Exception:
        return False


def verificar_backup(path: pathlib.Path | str) -> bool:
    caminho = pathlib.Path(path).resolve()
    if not caminho.is_file():
        return False
    uri = f"{caminho.as_uri()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        try:
            res = con.execute("PRAGMA integrity_check").fetchone()
            if res is not None and str(res[0]).strip().lower() == "ok":
                return True
            return False
        finally:
            con.close()
    except Exception:
        return False
