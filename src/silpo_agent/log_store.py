"""Reorder Log Store: local append-only audit trail of past Reorder flow runs,
plus Substitution Memory (item -> chosen replacement). Read/write only, no
business logic.
"""

import json
from pathlib import Path

DEFAULT_PATH = Path.home() / ".silpo-agent" / "reorder_log.json"

_EMPTY = {"runs": [], "substitutions": {}}


class ReorderLogStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_PATH

    def _load(self) -> dict:
        try:
            raw = self.path.read_text()
        except OSError:
            return dict(_EMPTY, runs=[], substitutions={})
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return dict(_EMPTY, runs=[], substitutions={})
        if not isinstance(data, dict) or "runs" not in data or "substitutions" not in data:
            return dict(_EMPTY, runs=[], substitutions={})
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    def append_run(self, run: dict) -> None:
        # ponytail: load-mutate-save, not atomic — two append_run calls in
        # close succession (e.g. within one `reorder` run) can race, the
        # second's _load happening before the first's _save lands, losing
        # a write. Fine today: the only caller is
        # address_resolver.resolve_address, once per `reorder` run (via
        # cli.py). If a future ticket adds a second append_run per run
        # (e.g. a full run record alongside this address audit record),
        # merge both into a single append_run call here instead of adding
        # file locking.
        data = self._load()
        data["runs"].append(run)
        self._save(data)

    def read_history(self) -> list[dict]:
        return self._load()["runs"]

    def set_substitution(self, item_id: str, replacement_id: str) -> None:
        data = self._load()
        data["substitutions"][item_id] = replacement_id
        self._save(data)

    def get_substitution(self, item_id: str) -> str | None:
        return self._load()["substitutions"].get(item_id)
