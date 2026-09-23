"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from datetime import datetime, timezone

from PySide6.QtCore import QThread

from ..common.bi_sync_store import BiSyncStore
from ..common.i18n import tr
from ..common.log import get_logger
from ..service.bi_sync_service import (
    PHASE_PULL,
    BiSyncService,
)
from ..service.sync_service import (
    PHASE_DELETE,
    PHASE_SCAN_LOCAL,
    PHASE_SCAN_REMOTE,
    PHASE_UPLOAD,
)

logger = get_logger(__name__)


class BiSyncRunThread(QThread):
    """双向同步运行线程：在后台执行一次完整双向同步。

    通过 _SyncJobSignals 将进度/状态/结果发射回主线程。
    支持 cancel() 中止（上传在分片边界、下载通过 cancel_event 及时停止）。
    """

    def __init__(self, pan, job, signals):
        super().__init__()
        self._pan = pan
        self._job = job
        self.signals = signals
        self._cancel = False

    @property
    def is_cancelled(self):
        return self._cancel

    def cancel(self):
        """请求取消：置位后当前文件处理完成后中止。"""
        self._cancel = True

    def run(self):
        job_id = int(self._job["id"])
        job_name = self._job.get("name", "")
        started_at = datetime.now(timezone.utc).isoformat()
        stats = {
            "added": 0, "updated": 0, "downloaded": 0, "conflicts": 0,
            "deleted_remote": 0, "deleted_local": 0, "failed": 0,
            "skipped": 0,
        }
        store = BiSyncStore()

        def _progress(rel_path, current, total, phase):
            if rel_path:
                self.signals.file_progress.emit(job_id, rel_path, current, total)
            else:
                phase_text = {
                    PHASE_SCAN_LOCAL: tr("bisync.phase_scan_local", "扫描本地文件"),
                    PHASE_SCAN_REMOTE: tr("bisync.phase_scan_remote", "获取云端列表"),
                    PHASE_UPLOAD: tr("bisync.phase_upload", "上传"),
                    PHASE_PULL: tr("bisync.phase_download", "下载"),
                    PHASE_DELETE: tr("bisync.phase_delete", "删除"),
                }.get(phase, phase)
                self.signals.status.emit(job_id, phase_text)

        try:
            service = BiSyncService(self._pan._session, self._pan.user_name)
            success, stats = service.run_bi_sync(
                self._job, progress_callback=_progress, cancel=self,
                # token 过期时自动重登（与 UI 上传路径一致）
                refresh_session=self._pan.login,
            )
            cancelled = self.is_cancelled

            if cancelled:
                status = "cancelled"
            elif success:
                status = "completed"
            else:
                status = "failed"

            summary = self._build_summary(stats, cancelled)
            self.signals.finished.emit(
                job_id, success and not cancelled, summary, stats
            )
            self._record_history(store, job_id, job_name, started_at, status, stats)
        except Exception as e:
            logger.error("双向同步运行异常: job=%s, err=%s", job_name, e)
            summary = tr("bisync.error_run", "同步失败: {}").format(e)
            self.signals.finished.emit(job_id, False, summary, stats)
            self._record_history(
                store, job_id, job_name, started_at, "failed", stats, message=str(e)
            )

    @staticmethod
    def _build_summary(stats, cancelled):
        """构建运行结果摘要文本。"""
        parts = []
        if stats["added"]:
            parts.append(tr("bisync.sum_added", "新增 {}").format(stats["added"]))
        if stats["updated"]:
            parts.append(tr("bisync.sum_updated", "更新 {}").format(stats["updated"]))
        if stats["downloaded"]:
            parts.append(
                tr("bisync.sum_downloaded", "下载 {}").format(stats["downloaded"])
            )
        if stats["conflicts"]:
            parts.append(
                tr("bisync.sum_conflicts", "冲突 {}").format(stats["conflicts"])
            )
        if stats["deleted_remote"] + stats["deleted_local"]:
            parts.append(
                tr("bisync.sum_deleted", "删除 {}").format(
                    stats["deleted_remote"] + stats["deleted_local"]
                )
            )
        if stats["failed"]:
            parts.append(tr("bisync.sum_failed", "失败 {}").format(stats["failed"]))
        if cancelled:
            return tr("bisync.sum_cancelled", "已取消") + (
                "（" + "，".join(parts) + "）" if parts else ""
            )
        if not parts:
            return tr("bisync.sum_uptodate", "已是最新，无需同步")
        return "，".join(parts)

    @staticmethod
    def _record_history(store, job_id, job_name, started_at, status, stats, message=""):
        """写入双向同步历史记录。"""
        try:
            store.add_history(
                job_id=job_id,
                job_name=job_name,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                added=stats.get("added", 0),
                updated=stats.get("updated", 0),
                downloaded=stats.get("downloaded", 0),
                deleted=stats.get("deleted_remote", 0)
                + stats.get("deleted_local", 0),
                conflicts=stats.get("conflicts", 0),
                failed=stats.get("failed", 0),
                status=status,
                message=message,
            )
        except Exception as e:
            logger.error("记录双向同步历史失败: %s", e)
