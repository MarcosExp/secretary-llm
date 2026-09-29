import sqlite3

from app.ops.restore_check import verify, write


def copy_db(src, dst):
    """Consistent copy of a WAL-mode database, standing in for a Litestream restore."""
    source, target = sqlite3.connect(src), sqlite3.connect(dst)
    source.backup(target)
    source.close()
    target.close()


def test_restored_copy_with_probe_passes(tmp_path):
    live, restored = tmp_path / "live.db", tmp_path / "restored.db"
    token = write(live)
    copy_db(live, restored)
    assert verify(restored, token, live)


def test_copy_taken_before_probe_fails(tmp_path):
    live, restored = tmp_path / "live.db", tmp_path / "restored.db"
    write(live)
    copy_db(live, restored)
    newer_token = write(live)  # replication "missed" this write
    assert not verify(restored, newer_token, live)


def test_missing_table_fails(tmp_path):
    live, restored = tmp_path / "live.db", tmp_path / "restored.db"
    token = write(live)
    copy_db(live, restored)
    conn = sqlite3.connect(live)
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    assert not verify(restored, token, live)


def test_empty_restore_fails(tmp_path):
    live, restored = tmp_path / "live.db", tmp_path / "restored.db"
    token = write(live)
    sqlite3.connect(restored).close()
    assert not verify(restored, token, live)
