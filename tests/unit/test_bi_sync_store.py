"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from src.app.common.bi_sync_store import (
    CONFLICT_KEEP_BOTH,
    CONFLICT_LOCAL_WINS,
    CONFLICT_REMOTE_WINS,
    BiSyncStore,
)


class TestJobCRUD:
    def test_add_job_defaults(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="job1", local_path="/tmp/local", remote_dir_id=7
        )
        job = store.get_job(job_id)
        assert job["name"] == "job1"
        assert job["local_path"] == "/tmp/local"
        assert job["remote_dir_id"] == 7
        assert "direction" not in job  # 双向同步任务无 direction 字段
        assert int(job["enabled"]) == 1
        assert int(job["delete_remote"]) == 0
        assert int(job["delete_local"]) == 0
        assert job["conflict_policy"] == CONFLICT_KEEP_BOTH
        assert int(job["sync_on_startup"]) == 1
        assert int(job["interval_seconds"]) == 0

    def test_add_job_custom_options(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="job2", local_path="/tmp/local", remote_dir_id=1,
            interval_seconds=300, enabled=False, delete_remote=True,
            delete_local=True, conflict_policy=CONFLICT_REMOTE_WINS,
            sync_on_startup=False,
        )
        job = store.get_job(job_id)
        assert int(job["interval_seconds"]) == 300
        assert int(job["enabled"]) == 0
        assert int(job["delete_remote"]) == 1
        assert int(job["delete_local"]) == 1
        assert job["conflict_policy"] == CONFLICT_REMOTE_WINS
        assert int(job["sync_on_startup"]) == 0

    def test_update_job_partial(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="job3", local_path="/tmp/local", remote_dir_id=0
        )
        store.update_job(
            job_id, name="renamed", delete_local=True,
            conflict_policy=CONFLICT_LOCAL_WINS, sync_on_startup=False,
        )
        job = store.get_job(job_id)
        assert job["name"] == "renamed"
        assert int(job["delete_local"]) == 1
        assert job["conflict_policy"] == CONFLICT_LOCAL_WINS
        assert int(job["sync_on_startup"]) == 0
        # 未更新的字段保持原值
        assert job["local_path"] == "/tmp/local"
        assert int(job["delete_remote"]) == 0

    def test_set_job_enabled_and_last_run(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="job4", local_path="/tmp/local", remote_dir_id=0
        )
        store.set_job_enabled(job_id, False)
        assert int(store.get_job(job_id)["enabled"]) == 0
        store.set_job_last_run(job_id)
        assert store.get_job(job_id)["last_run_at"] != ""

    def test_get_jobs_enabled_only(self, tmp_db):
        store = BiSyncStore()
        a = store.add_job(name="a", local_path="/p/a", remote_dir_id=0)
        b = store.add_job(
            name="b", local_path="/p/b", remote_dir_id=0, enabled=False
        )
        assert [int(j["id"]) for j in store.get_jobs()] == [a, b]
        enabled = store.get_jobs(enabled_only=True)
        assert [int(j["id"]) for j in enabled] == [a]

    def test_delete_job_cascades(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="job5", local_path="/tmp/local", remote_dir_id=0
        )
        store.set_state(job_id, "a.txt", 10, 100, 10, 200)
        store.add_history(job_id, "job5", "t0", added=1)
        store.delete_job(job_id)
        assert store.get_job(job_id) is None
        assert store.get_state(job_id) == {}
        assert store.get_history() == []


class TestState:
    def test_set_get_state_roundtrip(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="s", local_path="/tmp/local", remote_dir_id=0
        )
        store.set_state(job_id, "a.txt", 10, 100, 10, 200)
        store.set_state(job_id, "b.txt", 20, None, 20, None)
        state = store.get_state(job_id)
        assert state["a.txt"] == {
            "local_size": 10, "local_mtime": 100,
            "remote_size": 10, "remote_updateat": 200,
        }
        assert state["b.txt"] == {
            "local_size": 20, "local_mtime": None,
            "remote_size": 20, "remote_updateat": None,
        }

    def test_set_state_overwrite(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="s", local_path="/tmp/local", remote_dir_id=0
        )
        store.set_state(job_id, "a.txt", 1, 1, 1, 1)
        store.set_state(job_id, "a.txt", 2, 2, 2, 2)
        state = store.get_state(job_id)
        assert state["a.txt"]["local_size"] == 2
        assert len(state) == 1

    def test_remove_and_clear_state(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="s", local_path="/tmp/local", remote_dir_id=0
        )
        store.set_state(job_id, "a.txt", 1, 1, 1, 1)
        store.set_state(job_id, "b.txt", 2, 2, 2, 2)
        store.remove_state(job_id, "a.txt")
        assert set(store.get_state(job_id)) == {"b.txt"}
        store.clear_state(job_id)
        assert store.get_state(job_id) == {}

    def test_state_isolated_between_jobs(self, tmp_db):
        store = BiSyncStore()
        a = store.add_job(name="a", local_path="/p/a", remote_dir_id=0)
        b = store.add_job(name="b", local_path="/p/b", remote_dir_id=0)
        store.set_state(a, "x.txt", 1, 1, 1, 1)
        assert store.get_state(b) == {}


class TestHistory:
    def test_add_and_get_history(self, tmp_db):
        store = BiSyncStore()
        job_id = store.add_job(
            name="h", local_path="/tmp/local", remote_dir_id=0
        )
        store.add_history(
            job_id, "h", "2026-01-01T00:00:00", finished_at="2026-01-01T00:01:00",
            added=1, updated=2, downloaded=3, deleted_remote=4, deleted_local=5,
            conflicts=6, failed=7, skipped=8, status="completed",
        )
        rows = store.get_history()
        assert len(rows) == 1
        row = rows[0]
        assert row["job_id"] == job_id
        assert row["downloaded"] == 3
        assert row["deleted_remote"] == 4
        assert row["deleted_local"] == 5
        assert row["conflicts"] == 6
        assert row["failed"] == 7
        assert row["skipped"] == 8
        assert row["status"] == "completed"
        assert "deleted" not in row  # 旧合并列不再出现在新记录查询中

    def test_job_history_and_clear(self, tmp_db):
        store = BiSyncStore()
        a = store.add_job(name="a", local_path="/p/a", remote_dir_id=0)
        b = store.add_job(name="b", local_path="/p/b", remote_dir_id=0)
        store.add_history(a, "a", "t0")
        store.add_history(b, "b", "t0")
        assert len(store.get_job_history(a)) == 1
        store.clear_history()
        assert store.get_history() == []


class TestHistoryMigration:
    def test_legacy_deleted_column_upgraded(self, tmp_path):
        """旧库（合并 deleted 列）初始化时自动补齐新列。"""
        import sqlite3
        from src.app.common.database import Database

        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE bi_sync_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL DEFAULT 0,
                job_name    TEXT NOT NULL DEFAULT '',
                started_at  TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                added       INTEGER NOT NULL DEFAULT 0,
                updated     INTEGER NOT NULL DEFAULT 0,
                downloaded  INTEGER NOT NULL DEFAULT 0,
                deleted     INTEGER NOT NULL DEFAULT 0,
                conflicts   INTEGER NOT NULL DEFAULT 0,
                failed      INTEGER NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'completed',
                message     TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.execute(
            "INSERT INTO bi_sync_history (job_id, job_name, started_at, deleted)"
            " VALUES (1, 'legacy', 't0', 7)"
        )
        conn.commit()
        conn.close()

        Database.set_path(db_path)
        try:
            Database()  # 触发建表与迁移
            store = BiSyncStore()
            rows = store.get_history()
            assert len(rows) == 1
            # 旧记录新列补 0（旧 deleted 无法区分云端/本地，不搬运）
            assert int(rows[0]["deleted_remote"]) == 0
            assert int(rows[0]["deleted_local"]) == 0
            assert int(rows[0]["skipped"]) == 0

            # 迁移后新写入使用新字段
            store.add_history(
                1, "legacy", "t1", deleted_remote=2, deleted_local=3, skipped=4
            )
            row = store.get_history()[0]
            assert int(row["deleted_remote"]) == 2
            assert int(row["deleted_local"]) == 3
            assert int(row["skipped"]) == 4
        finally:
            Database.reset()