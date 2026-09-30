"""Lägger in den fiktiva fyrstationsträffen i en tom state-katalog.

    PYTHONPATH=src:tests python3 tools/tmbox-rig/seed.py $RIG_DIR/state
"""
import sys
from pathlib import Path

from runtime_fixture import fictional_runtime_package
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore

database = Path(sys.argv[1]) / "trainmeet.db"
database.parent.mkdir(parents=True, exist_ok=True)
runtime = SQLiteRuntimeStore(database)
publication = runtime.install(fictional_runtime_package())
operations = SQLiteOperationsStore(database)
operations.ensure_publication(publication)
operations.close()
runtime.close()
print("installerad", publication.publication_id)
