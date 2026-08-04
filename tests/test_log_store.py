from silpo_agent.log_store import ReorderLogStore


def test_read_history_on_missing_file_starts_fresh(tmp_path):
    store = ReorderLogStore(tmp_path / "reorder_log.json")

    assert store.read_history() == []


def test_append_run_then_read_history_round_trips(tmp_path):
    store = ReorderLogStore(tmp_path / "reorder_log.json")
    run = {
        "timestamp": "2026-08-04T10:00:00",
        "items_added": ["milk", "bread"],
        "substitutions": {},
        "address": "Kyiv, Khreshchatyk 1",
        "total": 412.50,
    }

    store.append_run(run)

    assert store.read_history() == [run]


def test_append_run_appends_without_overwriting_prior_runs(tmp_path):
    store = ReorderLogStore(tmp_path / "reorder_log.json")
    first = {"timestamp": "2026-08-01T10:00:00", "items_added": ["milk"], "substitutions": {}, "address": "A", "total": 100}
    second = {"timestamp": "2026-08-04T10:00:00", "items_added": ["bread"], "substitutions": {}, "address": "A", "total": 50}

    store.append_run(first)
    store.append_run(second)

    assert store.read_history() == [first, second]


def test_corrupt_log_file_does_not_crash_and_starts_fresh(tmp_path):
    path = tmp_path / "reorder_log.json"
    path.write_text("{not valid json::")
    store = ReorderLogStore(path)

    assert store.read_history() == []


def test_append_run_recovers_after_corrupt_file(tmp_path):
    path = tmp_path / "reorder_log.json"
    path.write_text("{not valid json::")
    store = ReorderLogStore(path)
    run = {"timestamp": "2026-08-04T10:00:00", "items_added": ["milk"], "substitutions": {}, "address": "A", "total": 10}

    store.append_run(run)

    assert store.read_history() == [run]


def test_substitution_memory_set_then_get_round_trips(tmp_path):
    store = ReorderLogStore(tmp_path / "reorder_log.json")

    store.set_substitution("milk-1l", "milk-1l-oat")

    assert store.get_substitution("milk-1l") == "milk-1l-oat"


def test_substitution_memory_unknown_item_returns_none(tmp_path):
    store = ReorderLogStore(tmp_path / "reorder_log.json")

    assert store.get_substitution("never-set") is None


def test_concurrent_writes_do_not_lose_updates(tmp_path):
    """A single `reorder` run can call append_run (address audit) and
    set_substitution (once per freshly-chosen replacement) several times.
    Each is a load-mutate-save; without locking, one call's load can land
    between another's load and save and clobber it on write. Drive many
    concurrent writers at the shared store to prove the lock in `_locked`
    (log_store.py) serializes them instead of losing writes.
    """
    import threading

    store = ReorderLogStore(tmp_path / "reorder_log.json")
    writers = 8

    def write(i):
        store.set_substitution(f"item-{i}", f"replacement-{i}")
        store.append_run({"timestamp": f"t{i}", "worker": i})

    threads = [threading.Thread(target=write, args=(i,)) for i in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = store._load()
    assert len(data["substitutions"]) == writers
    assert len(data["runs"]) == writers
