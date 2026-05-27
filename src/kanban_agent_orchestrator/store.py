import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

from kanban_agent_orchestrator.models import Snapshot


class JsonStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("KANBAN_ORCHESTRATOR_DB", "./data/orchestrator.json"))
        self.lock = threading.RLock()

    def load(self) -> Snapshot:
        with self.lock:
            if not self.path.exists():
                return Snapshot()
            return Snapshot.model_validate_json(self.path.read_text())

    def save(self, snapshot: Snapshot) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = snapshot.model_dump_json(indent=2)
            with NamedTemporaryFile("w", dir=self.path.parent, delete=False) as temp_file:
                temp_file.write(data)
                temp_path = Path(temp_file.name)
            temp_path.replace(self.path)
