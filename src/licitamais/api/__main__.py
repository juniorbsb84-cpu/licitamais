import argparse
from pathlib import Path

import uvicorn

from . import create_app

PADRAO_STATIC = Path(__file__).resolve().parents[3] / "frontend" / "dist"

p = argparse.ArgumentParser(description="API v2 do LicitamAIs")
p.add_argument("--db", required=True)
p.add_argument("--porta", type=int, default=8090)
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--static", default=str(PADRAO_STATIC) if PADRAO_STATIC.is_dir() else None, help="pasta frontend/dist")
a = p.parse_args()
uvicorn.run(create_app(a.db, a.static), host=a.host, port=a.porta)
