import pathlib
import sqlite3

from .migrations import apply_migrations


def init_schema(con: sqlite3.Connection) -> None:
    migrations_dir = pathlib.Path(__file__).resolve().parents[2] / "migrations"
    apply_migrations(con, migrations_dir)
