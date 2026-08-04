"""Reorder Log Store: local append-only audit trail of past Reorder flow runs,
plus Substitution Memory (item -> chosen replacement). Read/write only, no
business logic.
"""

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PATH = Path.home() / ".silpo-agent" / "reorder_log.json"

_EMPTY = {"runs": [], "substitutions": {}}


class ReorderLogStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_PATH

    @contextmanager
    def _locked(self):
        # ponytail: advisory file lock (fcntl, POSIX-only) around each
        # load-mutate-save, not a cross-platform lock library — fine for
        # this single-machine personal CLI. A single `reorder` run can now
        # call append_run (address audit) and set_substitution (once per
        # freshly-chosen replacement) multiple times; the lock serializes
        # each call's load+save into one critical section so a later call
        # can never load a stale copy and clobber an earlier call's save.
        # Swap for `filelock` (already-installed-dependency rung) if this
        # ever needs to run on Windows.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with open(lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

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
        with self._locked():
            data = self._load()
            data["runs"].append(run)
            self._save(data)

    def read_history(self) -> list[dict]:
        return self._load()["runs"]

    def set_substitution(self, item_id: str, replacement_id: str) -> None:
        with self._locked():
            data = self._load()
            data["substitutions"][item_id] = replacement_id
            self._save(data)

    def get_substitution(self, item_id: str) -> str | None:
        return self._load()["substitutions"].get(item_id)
