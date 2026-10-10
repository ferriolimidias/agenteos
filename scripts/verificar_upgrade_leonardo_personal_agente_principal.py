from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verificar_upgrade_leonardo_metodo_hibrido_97 import main


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
