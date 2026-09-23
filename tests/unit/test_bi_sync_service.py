"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.app.common.bi_sync_store import (
    CONFLICT_KEEP_BOTH,
    CONFLICT_LOCAL_WINS,
    CONFLICT_REMOTE_WINS,
    BiSyncStore,
)
from src.app.service.bi_sync_service import BiSyncService


class _DummySession:
    """最小可补丁的 session 替身。"""

    def trash_file(self, *args, **kwargs):
        return SimpleNamespace(code=0)


def _make_svc():
    return BiSyncService(_DummySession())


def _make_job(**overrides):
    job = {
        "id": 1,
        "name": "test_job",
        "local_path": "/tmp/nonexistent",
        "remote_dir_id": 0,
        "remote_dir_name": "",
        "interval_seconds": 0,
        "enabled": 1,
        "delete_remote": 0,
        "delete_local": 0,
        "conflict_policy": CONFLICT_KEEP_BOTH,
        "sync_on_startup": 1,
    }
    job.update(overrides)
    return job


def _local_file(size=10, mtime=100, abs_path="/tmp/nonexistent/a.txt"):
    return {"size": size, "mtime": mtime, "abs": abs_path, "is_dir": False}


def _local_dir():
    return {"size": 0, "mtime": 100, "abs": "/tmp/nonexistent/sub", "is_dir": True}


def _remote_file(fid=1, size=10, updateat=200):
    return {
        "FileId": fid, "FileName": "a.txt", "Type": 0,
        "Size": size, "UpdateAt": updateat,
    }


def _remote_dir(fid=9):
    return {"FileId": fid, "FileName": "sub", "Type": 1, "Size": 0, "UpdateAt": 0}


def _get_state(svc, job_id=1):
    return svc._store.get_state(job_id)


class _Cancel:
    def __init__(self, flag=False):
        self.is_cancelled = flag


class TestComputeAdoption:
    def test_first_contact_same_size_adopts_state(self, tmp_db):
        svc = _make_svc()
        local = {"a.txt": _local_file(size=10, mtime=100)}
        remote = {"a.txt": _remote_file(size=10, updateat=200)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert plan["push_uploads"] == []
        assert plan["pull_downloads"] == []
        assert plan["conflicts"] == []
        st = _get_state(svc)["a.txt"]
        assert st["local_size"] == 10
        assert st["remote_updateat"] == 200

    def test_first_contact_size_diff_is_conflict_keep_both(self, tmp_db):
        svc = _make_svc()
        local = {"a.txt": _local_file(size=10)}
        remote = {"a.txt": _remote_file(size=20)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert len(plan["conflicts"]) == 1
        # 保留双方副本：主路径上传本地版本
        assert len(plan["push_uploads"]) == 1
        assert plan["push_uploads"][0][3] is False

    def test_first_contact_size_diff_local_wins(self, tmp_db):
        svc = _make_svc()
        local = {"a.txt": _local_file(size=10)}
        remote = {"a.txt": _remote_file(size=20)}
        job = _make_job(conflict_policy=CONFLICT_LOCAL_WINS)
        plan = svc.compute_bi_changes(job, local, remote)
        assert len(plan["push_uploads"]) == 1
        assert plan["conflicts"] == []
        assert plan["pull_downloads"] == []

    def test_first_contact_size_diff_remote_wins(self, tmp_db):
        svc = _make_svc()
        local = {"a.txt": _local_file(size=10)}
        remote = {"a.txt": _remote_file(size=20)}
        job = _make_job(conflict_policy=CONFLICT_REMOTE_WINS)
        plan = svc.compute_bi_changes(job, local, remote)
        assert len(plan["pull_downloads"]) == 1
        assert plan["conflicts"] == []
        assert plan["push_uploads"] == []


class TestComputeChanges:
    def _seed(self, svc, rel="a.txt", ls=10, lm=100, rs=10, ru=200):
        svc._store.set_state(1, rel, ls, lm, rs, ru)

    def test_local_changed_only_pushes(self, tmp_db):
        svc = _make_svc()
        self._seed(svc)
        local = {"a.txt": _local_file(size=15, mtime=300)}
        remote = {"a.txt": _remote_file(size=10, updateat=200)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert [(r, n) for r, _, _, n in plan["push_uploads"]] == [("a.txt", False)]
        assert plan["pull_downloads"] == []

    def test_remote_changed_only_pulls(self, tmp_db):
        svc = _make_svc()
        self._seed(svc)
        local = {"a.txt": _local_file(size=10, mtime=100)}
        remote = {"a.txt": _remote_file(size=15, updateat=500)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert plan["pull_downloads"] == [("a.txt", remote["a.txt"])]
        assert plan["push_uploads"] == []

    def test_both_changed_conflict_policies(self, tmp_db):
        # keep_both（默认）：冲突 + 主路径上传本地版
        svc = _make_svc()
        self._seed(svc)
        local = {"a.txt": _local_file(size=15, mtime=300)}
        remote = {"a.txt": _remote_file(size=20, updateat=500)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert len(plan["conflicts"]) == 1
        assert len(plan["push_uploads"]) == 1

        # local_wins：只上传
        svc2 = _make_svc()
        self._seed(svc2)
        plan = svc2.compute_bi_changes(
            _make_job(conflict_policy=CONFLICT_LOCAL_WINS), local, remote
        )
        assert plan["conflicts"] == []
        assert len(plan["push_uploads"]) == 1

        # remote_wins：只下载
        svc3 = _make_svc()
        self._seed(svc3)
        plan = svc3.compute_bi_changes(
            _make_job(conflict_policy=CONFLICT_REMOTE_WINS), local, remote
        )
        assert plan["conflicts"] == []
        assert len(plan["pull_downloads"]) == 1

    def test_unchanged_is_noop(self, tmp_db):
        svc = _make_svc()
        self._seed(svc)
        local = {"a.txt": _local_file(size=10, mtime=100)}
        remote = {"a.txt": _remote_file(size=10, updateat=200)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert plan["push_uploads"] == []
        assert plan["pull_downloads"] == []
        assert plan["conflicts"] == []

    def test_remote_updateat_none_heals_without_pull(self, tmp_db):
        """上传后 remote_updateat 未知（None）：云端大小一致 → 不拉取，仅补齐时间戳。"""
        svc = _make_svc()
        self._seed(svc, rs=10, ru=None)
        local = {"a.txt": _local_file(size=10, mtime=100)}
        remote = {"a.txt": _remote_file(size=10, updateat=900)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert plan["pull_downloads"] == []
        assert plan["push_uploads"] == []
        assert _get_state(svc)["a.txt"]["remote_updateat"] == 900

    def test_remote_updateat_none_size_diff_pulls(self, tmp_db):
        svc = _make_svc()
        self._seed(svc, rs=10, ru=None)
        local = {"a.txt": _local_file(size=10, mtime=100)}
        remote = {"a.txt": _remote_file(size=99, updateat=900)}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert len(plan["pull_downloads"]) == 1

    def test_local_new_file_pushes_as_new(self, tmp_db):
        svc = _make_svc()
        local = {"a.txt": _local_file()}
        plan = svc.compute_bi_changes(_make_job(), local, {})
        assert [(r, n) for r, _, _, n in plan["push_uploads"]] == [("a.txt", True)]

    def test_remote_new_file_pulls(self, tmp_db):
        svc = _make_svc()
        remote = {"a.txt": _remote_file()}
        plan = svc.compute_bi_changes(_make_job(), {}, remote)
        assert plan["pull_downloads"] == [("a.txt", remote["a.txt"])]


class TestComputeDeletes:
    def test_local_deleted_remote_unchanged_delete_remote(self, tmp_db):
        svc = _make_svc()
        svc._store.set_state(1, "a.txt", 10, 100, 10, 200)
        remote = {"a.txt": _remote_file()}
        plan = svc.compute_bi_changes(
            _make_job(delete_remote=True), {}, remote
        )
        assert plan["delete_remote"] == ["a.txt"]

    def test_local_deleted_remote_unchanged_keep(self, tmp_db):
        svc = _make_svc()
        svc._store.set_state(1, "a.txt", 10, 100, 10, 200)
        remote = {"a.txt": _remote_file()}
        plan = svc.compute_bi_changes(_make_job(), {}, remote)
        assert plan["delete_remote"] == []
        assert plan["pull_downloads"] == []

    def test_local_deleted_remote_changed_restores(self, tmp_db):
        """本地删了文件，但云端又改了 → 拉回本地（不删除云端）。"""
        svc = _make_svc()
        svc._store.set_state(1, "a.txt", 10, 100, 10, 200)
        remote = {"a.txt": _remote_file(size=30, updateat=800)}
        plan = svc.compute_bi_changes(
            _make_job(delete_remote=True), {}, remote
        )
        assert plan["delete_remote"] == []
        assert len(plan["pull_downloads"]) == 1

    def test_remote_deleted_local_unchanged_delete_local(self, tmp_db):
        svc = _make_svc()
        svc._store.set_state(1, "a.txt", 10, 100, 10, 200)
        local = {"a.txt": _local_file()}
        plan = svc.compute_bi_changes(
            _make_job(delete_local=True), local, {}
        )
        assert plan["delete_local"] == ["a.txt"]

    def test_remote_deleted_local_unchanged_keep(self, tmp_db):
        svc = _make_svc()
        svc._store.set_state(1, "a.txt", 10, 100, 10, 200)
        local = {"a.txt": _local_file()}
        plan = svc.compute_bi_changes(_make_job(), local, {})
        assert plan["delete_local"] == []

    def test_remote_deleted_local_changed_repush(self, tmp_db):
        """云端删了文件，但本地又改了 → 重新上传（不删除本地）。"""
        svc = _make_svc()
        svc._store.set_state(1, "a.txt", 10, 100, 10, 200)
        local = {"a.txt": _local_file(size=50, mtime=900)}
        plan = svc.compute_bi_changes(
            _make_job(delete_local=True), local, {}
        )
        assert plan["delete_local"] == []
        assert [(r, n) for r, _, _, n in plan["push_uploads"]] == [("a.txt", True)]


class TestComputeDirs:
    def test_local_dir_missing_remote_creates_cloud_dir(self, tmp_db):
        svc = _make_svc()
        local = {"sub": _local_dir(), "sub/f.txt": _local_file()}
        plan = svc.compute_bi_changes(_make_job(), local, {})
        assert ("sub", "") in plan["push_dirs"]
        assert [r for r, _, _, _ in plan["push_uploads"]] == ["sub/f.txt"]

    def test_remote_dir_missing_local_creates_local_dir(self, tmp_db):
        svc = _make_svc()
        remote = {"sub": _remote_dir(), "sub/f.txt": _remote_file()}
        plan = svc.compute_bi_changes(_make_job(), {}, remote)
        assert "sub" in plan["pull_dirs"]
        assert [r for r, _ in plan["pull_downloads"]] == ["sub/f.txt"]

    def test_type_conflict_file_vs_dir(self, tmp_db):
        svc = _make_svc()
        local = {"sub": _local_file()}
        remote = {"sub": _remote_dir()}
        plan = svc.compute_bi_changes(_make_job(), local, remote)
        assert plan["type_conflicts"] == ["sub"]
        assert plan["push_uploads"] == []

    def test_stale_state_cleaned_when_both_gone(self, tmp_db):
        svc = _make_svc()
        svc._store.set_state(1, "ghost.txt", 1, 1, 1, 1)
        plan = svc.compute_bi_changes(_make_job(), {}, {})
        assert plan["delete_remote"] == []
        assert _get_state(svc) == {}


class TestRunBiSync:
    def _prepare_local(self, tmp_path):
        root = tmp_path / "local"
        root.mkdir(exist_ok=True)
        return root

    def test_local_dir_missing_aborts(self, tmp_db, tmp_path):
        svc = _make_svc()
        job = _make_job(local_path=str(tmp_path / "nope"))
        success, stats = svc.run_bi_sync(job)
        assert success is False
        assert stats["failed"] == 0

    def test_remote_index_failure_aborts(self, tmp_db, tmp_path):
        """远端索引失败必须中止（防误删安全闸）。"""
        root = self._prepare_local(tmp_path)
        svc = _make_svc()
        svc.build_remote_index = MagicMock(return_value=None)
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job)
        assert success is False
        assert stats["deleted_remote"] == 0

    def test_cancel_before_run(self, tmp_db, tmp_path):
        root = self._prepare_local(tmp_path)
        svc = _make_svc()
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job, cancel=_Cancel(True))
        assert success is False

    def test_noop_success(self, tmp_db, tmp_path):
        root = self._prepare_local(tmp_path)
        (root / "a.txt").write_bytes(b"hello")
        svc = _make_svc()
        svc.build_remote_index = MagicMock(
            return_value={"a.txt": _remote_file(size=5)}
        )
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job)
        assert success is True
        assert stats == {
            "added": 0, "updated": 0, "downloaded": 0, "conflicts": 0,
            "deleted_remote": 0, "deleted_local": 0, "failed": 0,
            "skipped": 0,
        }

    def test_upload_new_file(self, tmp_db, tmp_path):
        root = self._prepare_local(tmp_path)
        (root / "a.txt").write_bytes(b"hello")
        svc = _make_svc()
        svc.build_remote_index = MagicMock(return_value={})
        svc._upload.up_load = MagicMock(return_value=123)
        # 上传后云端验证：目录列表能查到该文件
        svc._file.get_dir_by_id = MagicMock(
            return_value=(0, [{"FileName": "a.txt", "Size": 5, "UpdateAt": 999}], 1, True, 1)
        )
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job)
        assert success is True
        assert stats["added"] == 1
        svc._upload.up_load.assert_called_once()
        # 统一覆盖语义，避免 5060 竞态
        assert svc._upload.up_load.call_args.kwargs.get("dup_choice") == 2
        st = _get_state(svc)["a.txt"]
        assert st["local_size"] == 5
        # 快照直接取自云端验证列表（时间戳已确认）
        assert st["remote_updateat"] == 999

    def test_upload_passes_refresh_session(self, tmp_db, tmp_path):
        """token 过期时 up_load 应收到 refresh_session 回调（自动重登）。"""
        root = self._prepare_local(tmp_path)
        (root / "a.txt").write_bytes(b"hello")
        svc = _make_svc()
        svc.build_remote_index = MagicMock(return_value={})
        svc._upload.up_load = MagicMock(return_value=123)
        svc._file.get_dir_by_id = MagicMock(
            return_value=(0, [{"FileName": "a.txt", "Size": 5, "UpdateAt": 999}], 1, True, 1)
        )
        refresh_fn = MagicMock(return_value=200)
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job, refresh_session=refresh_fn)
        assert success is True
        assert svc._upload.up_load.call_args.kwargs.get("refresh_session") is refresh_fn

    def test_upload_verification_failed_no_state(self, tmp_db, tmp_path, monkeypatch):
        """服务端静默未注册时：计失败、不写快照，下次同步自动重传。"""
        monkeypatch.setattr(
            "src.app.service.bi_sync_service.time.sleep", lambda s: None
        )
        root = self._prepare_local(tmp_path)
        (root / "a.txt").write_bytes(b"hello")
        svc = _make_svc()
        svc.build_remote_index = MagicMock(return_value={})
        svc._upload.up_load = MagicMock(return_value=123)
        # 验证列表始终查不到该文件
        svc._file.get_dir_by_id = MagicMock(
            return_value=(0, [], 0, True, 1)
        )
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job)
        assert success is True  # 单文件失败不中止整个同步
        assert stats["failed"] == 1
        assert stats["added"] == 0
        # 不写快照 → 本地文件不被误删，下次同步重传
        assert _get_state(svc) == {}

    def test_delete_local_guard_unconfirmed_remote(self, tmp_db):
        """云端从未被确认存在（上次上传未验证）时绝不删本地，改为重新上传。"""
        svc = _make_svc()
        # 上传后未验证成功的快照：remote_updateat=None
        svc._store.set_state(1, "a.txt", 10, 100, 10, None)
        local = {"a.txt": _local_file(size=10, mtime=100)}
        plan = svc.compute_bi_changes(
            _make_job(delete_local=True), local, {}
        )
        assert plan["delete_local"] == []
        assert [(r, n) for r, _, _, n in plan["push_uploads"]] == [("a.txt", True)]

    def test_download_remote_file(self, tmp_db, tmp_path):
        root = self._prepare_local(tmp_path)
        svc = _make_svc()
        svc.build_remote_index = MagicMock(
            return_value={"a.txt": _remote_file(size=5, updateat=1_700_000_000)}
        )
        svc._download.link_by_fileDetail = MagicMock(return_value="http://x/a")
        svc._download.download_file = MagicMock(return_value=True)
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job)
        assert success is True
        assert stats["downloaded"] == 1
        svc._download.download_file.assert_called_once()
        st = _get_state(svc)["a.txt"]
        assert st["remote_updateat"] == 1_700_000_000
        assert st["local_mtime"] == 1_700_000_000

    def test_download_link_error_counts_failed(self, tmp_db, tmp_path):
        root = self._prepare_local(tmp_path)
        svc = _make_svc()
        svc.build_remote_index = MagicMock(
            return_value={"a.txt": _remote_file()}
        )
        svc._download.link_by_fileDetail = MagicMock(return_value=5113)
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job)
        assert success is True  # 单文件失败不中止整个同步
        assert stats["failed"] == 1
        assert stats["downloaded"] == 0

    def test_conflict_keep_both(self, tmp_db, tmp_path):
        root = self._prepare_local(tmp_path)
        (root / "a.txt").write_bytes(b"local-version")
        svc = _make_svc()
        svc._store.set_state(1, "a.txt", 5, 100, 5, 200)
        # 本地改（mtime 变化）+ 云端改（size 变化）
        svc.build_remote_index = MagicMock(
            return_value={"a.txt": _remote_file(size=20, updateat=1_700_000_000)}
        )
        svc._upload.up_load = MagicMock(return_value=123)
        # 上传后云端验证：目录列表能查到该文件
        svc._file.get_dir_by_id = MagicMock(
            return_value=(
                0,
                [{"FileName": "a.txt", "Size": 13, "UpdateAt": 1_700_000_000}],
                1, True, 1,
            )
        )
        svc._download.link_by_fileDetail = MagicMock(return_value="http://x/a")

        def _fake_download(url, target, size, **kwargs):
            target.write_bytes(b"x" * min(size, 64))
            return True

        svc._download.download_file = MagicMock(side_effect=_fake_download)
        job = _make_job(local_path=str(root))
        success, stats = svc.run_bi_sync(job)
        assert success is True
        assert stats["conflicts"] == 1
        assert stats["updated"] == 1
        assert stats["downloaded"] == 1
        # 副本文件已创建在本地
        names = os.listdir(root)
        assert "a.txt" in names
        copies = [n for n in names if "冲突副本" in n]
        assert len(copies) == 1
        # 原始路径快照已更新，副本无快照（下次同步自动上传）
        st = _get_state(svc)
        assert "a.txt" in st
        assert not any("冲突副本" in k for k in st)

    def test_delete_remote_and_local(self, tmp_db, tmp_path):
        root = self._prepare_local(tmp_path)
        (root / "a.txt").write_bytes(b"x")
        (root / "b.txt").write_bytes(b"y")
        svc = _make_svc()
        # 快照 mtime 必须与磁盘真实值一致（否则会被误判为本地修改）
        st_a = (root / "a.txt").stat()
        svc._store.set_state(1, "a.txt", st_a.st_size, int(st_a.st_mtime), 1, 200)
        svc._store.set_state(1, "b.txt", 1, 100, 1, 200)
        (root / "b.txt").unlink()
        # 云端：b.txt 仍在（本地删了 → 删云端）；a.txt 已不存在（云端删了 → 删本地）
        svc.build_remote_index = MagicMock(
            return_value={
                "b.txt": _remote_file(size=1),
            }
        )
        svc._session.trash_file = MagicMock(
            return_value=SimpleNamespace(code=0)
        )
        job = _make_job(
            local_path=str(root), delete_remote=True, delete_local=True
        )
        success, stats = svc.run_bi_sync(job)
        assert success is True
        # 本地删了 b → 删云端 b；云端删了 a → 删本地 a
        assert stats["deleted_remote"] == 1
        assert stats["deleted_local"] == 1
        svc._session.trash_file.assert_called_once()
        assert not (root / "a.txt").exists()
        assert _get_state(svc) == {}