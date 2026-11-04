import os
import time

from realestate.entrypoints import healthcheck
from realestate.infrastructure.observability.metrics import Heartbeat, heartbeat_age


def test_heartbeat_file_and_healthcheck(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_DIR", str(tmp_path))
    from realestate.infrastructure.config.settings import load_settings

    load_settings.cache_clear()
    try:
        assert heartbeat_age("bronze", str(tmp_path)) is None
        assert healthcheck.main(["bronze"]) == 1  # never beat

        Heartbeat("bronze", str(tmp_path), min_interval=0).beat()
        assert healthcheck.main(["bronze", "--max-age", "60"]) == 0

        stale = time.time() - 600
        os.utime(tmp_path / "realestate-bronze.heartbeat", (stale, stale))
        assert healthcheck.main(["bronze", "--max-age", "60"]) == 1
    finally:
        load_settings.cache_clear()


def test_heartbeat_is_throttled(tmp_path):
    hb = Heartbeat("x", str(tmp_path), min_interval=60)
    hb.beat()
    first = (tmp_path / "realestate-x.heartbeat").stat().st_mtime_ns
    hb.beat()
    assert (tmp_path / "realestate-x.heartbeat").stat().st_mtime_ns == first


def test_backup_age_ignores_unfinished_dumps(tmp_path):
    from realestate.infrastructure.observability.metrics import (
        BACKUP_LAST_SUCCESS,
        export_backup_ages,
    )

    (tmp_path / "postgres").mkdir()
    done = tmp_path / "postgres" / "db_1.dump"
    done.write_text("x")
    os.utime(done, (1_700_000_000, 1_700_000_000))
    (tmp_path / "postgres" / "db_2.dump.tmp").write_text("partial")

    ages = export_backup_ages(str(tmp_path))
    assert ages == {"postgres": 1_700_000_000, "mongo": None}
    assert BACKUP_LAST_SUCCESS.labels("postgres")._value.get() == 1_700_000_000
    assert BACKUP_LAST_SUCCESS.labels("mongo")._value.get() == 0  # never backed up -> alert fires
