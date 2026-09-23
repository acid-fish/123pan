"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from datetime import datetime, timezone

from .database import Database
from .log import get_logger

logger = get_logger(__name__)

# 冲突处理策略
CONFLICT_KEEP_BOTH = "keep_both"    # 保留双方副本（默认）：本地主路径保留本地版本，云端版本另存冲突副本
CONFLICT_LOCAL_WINS = "local_wins"  # 以本地为准：上传覆盖云端
CONFLICT_REMOTE_WINS = "remote_wins"  # 以云端为准：下载覆盖本地

CONFLICT_POLICIES = (
    CONFLICT_KEEP_BOTH,
    CONFLICT_LOCAL_WINS,
    CONFLICT_REMOTE_WINS,
)

# 同步间隔（秒）：0 = 手动
INTERVAL_MANUAL = 0
INTERVAL_30S = 30
INTERVAL_1M = 60
INTERVAL_5M = 300
INTERVAL_30M = 1800
INTERVAL_1H = 3600


class BiSyncStore:
    """双向同步任务持久化存储（SQLite）。

    与 SyncStore（单向同步）完全独立，互不影响。

    表：
        bi_sync_jobs    -- 双向同步任务配置
        bi_sync_history -- 每次运行结果
        bi_sync_state   -- 每个文件的本地/云端版本快照
    """

    def __init__(self):
        self._db = Database()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    # ---- 同步任务 ----

    def add_job(
        self,
        name,
        local_path,
        remote_dir_id,
        remote_dir_name="",
        interval_seconds=INTERVAL_MANUAL,
        enabled=True,
        delete_remote=False,
        delete_local=False,
        conflict_policy=CONFLICT_KEEP_BOTH,
        sync_on_startup=True,
    ):
        """新增双向同步任务，返回自增 ID。"""
        now = self._now()
        cur = self._db.execute(
            """INSERT INTO bi_sync_jobs
               (name, local_path, remote_dir_id, remote_dir_name,
                interval_seconds, enabled, delete_remote, delete_local,
                conflict_policy, sync_on_startup, last_run_at,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
            (
                name,
                local_path,
                int(remote_dir_id),
                remote_dir_name,
                int(interval_seconds),
                1 if enabled else 0,
                1 if delete_remote else 0,
                1 if delete_local else 0,
                conflict_policy,
                1 if sync_on_startup else 0,
                now,
                now,
            ),
        )
        return cur.lastrowid

    def update_job(
        self, job_id, name=None, local_path=None, remote_dir_id=None,
        remote_dir_name=None, interval_seconds=None, delete_remote=None,
        delete_local=None, conflict_policy=None, sync_on_startup=None,
    ):
        """更新双向同步任务配置（仅更新非 None 字段）。"""
        fields = []
        params = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if local_path is not None:
            fields.append("local_path = ?")
            params.append(local_path)
        if remote_dir_id is not None:
            fields.append("remote_dir_id = ?")
            params.append(int(remote_dir_id))
        if remote_dir_name is not None:
            fields.append("remote_dir_name = ?")
            params.append(remote_dir_name)
        if interval_seconds is not None:
            fields.append("interval_seconds = ?")
            params.append(int(interval_seconds))
        if delete_remote is not None:
            fields.append("delete_remote = ?")
            params.append(1 if delete_remote else 0)
        if delete_local is not None:
            fields.append("delete_local = ?")
            params.append(1 if delete_local else 0)
        if conflict_policy is not None:
            fields.append("conflict_policy = ?")
            params.append(conflict_policy)
        if sync_on_startup is not None:
            fields.append("sync_on_startup = ?")
            params.append(1 if sync_on_startup else 0)
        if not fields:
            return
        params.append(self._now())
        params.append(int(job_id))
        self._db.execute(
            "UPDATE bi_sync_jobs SET " + ", ".join(fields) + ", updated_at = ?"
            " WHERE id = ?",
            tuple(params),
        )

    def set_job_enabled(self, job_id, enabled):
        """启用/禁用双向同步任务。"""
        self._db.execute(
            "UPDATE bi_sync_jobs SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, self._now(), int(job_id)),
        )

    def set_job_last_run(self, job_id):
        """记录任务最近运行时间。"""
        self._db.execute(
            "UPDATE bi_sync_jobs SET last_run_at = ?, updated_at = ? WHERE id = ?",
            (self._now(), self._now(), int(job_id)),
        )

    def delete_job(self, job_id):
        """删除双向同步任务（含其历史记录与状态快照）。"""
        self._db.execute("DELETE FROM bi_sync_jobs WHERE id = ?", (int(job_id),))
        self._db.execute(
            "DELETE FROM bi_sync_history WHERE job_id = ?", (int(job_id),)
        )
        self._db.execute(
            "DELETE FROM bi_sync_state WHERE job_id = ?", (int(job_id),)
        )

    def get_jobs(self, enabled_only=False):
        """查询全部双向同步任务（按创建顺序）。"""
        sql = "SELECT * FROM bi_sync_jobs"
        params = ()
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        return self._db.query(sql, params)

    def get_job(self, job_id):
        """查询单个双向同步任务。"""
        return self._db.query_one(
            "SELECT * FROM bi_sync_jobs WHERE id = ?", (int(job_id),)
        )

    # ---- 运行历史 ----

    def add_history(
        self, job_id, job_name, started_at, finished_at="",
        added=0, updated=0, downloaded=0, deleted=0, conflicts=0,
        failed=0, status="completed", message="",
    ):
        """记录一次双向同步运行结果。"""
        self._db.execute(
            """INSERT INTO bi_sync_history
               (job_id, job_name, started_at, finished_at,
                added, updated, downloaded, deleted, conflicts,
                failed, status, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(job_id),
                job_name,
                started_at,
                finished_at,
                int(added),
                int(updated),
                int(downloaded),
                int(deleted),
                int(conflicts),
                int(failed),
                status,
                message,
            ),
        )

    def get_history(self, limit=100):
        """查询双向同步历史（最新在前）。"""
        return self._db.query(
            "SELECT * FROM bi_sync_history ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )

    def get_job_history(self, job_id, limit=50):
        """查询单个任务的同步历史。"""
        return self._db.query(
            "SELECT * FROM bi_sync_history WHERE job_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (int(job_id), int(limit)),
        )

    def clear_history(self):
        """清空同步历史。"""
        self._db.execute("DELETE FROM bi_sync_history")
        logger.info("双向同步历史已清空")

    # ---- 文件版本快照 ----

    def set_state(
        self, job_id, rel_path,
        local_size, local_mtime, remote_size, remote_updateat,
    ):
        """记录文件两端的版本快照。

        local_mtime / remote_updateat 允许为 None（未知）：
        上传后云端时间戳未知，先记 None，下次扫描时用远端索引补齐。
        """
        self._db.execute(
            """INSERT OR REPLACE INTO bi_sync_state
               (job_id, rel_path, local_size, local_mtime,
                remote_size, remote_updateat)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                int(job_id),
                rel_path,
                int(local_size),
                local_mtime,
                int(remote_size),
                remote_updateat,
            ),
        )

    def get_state(self, job_id):
        """获取任务的全部版本快照：{rel_path: {local_size, local_mtime, remote_size, remote_updateat}}。"""
        rows = self._db.query(
            "SELECT rel_path, local_size, local_mtime, remote_size, remote_updateat"
            " FROM bi_sync_state WHERE job_id = ?",
            (int(job_id),),
        )
        return {
            row["rel_path"]: {
                "local_size": int(row["local_size"] or 0),
                "local_mtime": row["local_mtime"],
                "remote_size": int(row["remote_size"] or 0),
                "remote_updateat": row["remote_updateat"],
            }
            for row in rows
        }

    def remove_state(self, job_id, rel_path):
        """删除单个文件的版本快照。"""
        self._db.execute(
            "DELETE FROM bi_sync_state WHERE job_id = ? AND rel_path = ?",
            (int(job_id), rel_path),
        )

    def clear_state(self, job_id):
        """清空任务全部版本快照。"""
        self._db.execute(
            "DELETE FROM bi_sync_state WHERE job_id = ?", (int(job_id),)
        )
