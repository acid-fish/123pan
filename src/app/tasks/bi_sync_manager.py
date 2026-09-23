"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from PySide6.QtCore import QTimer

from ..common.bi_sync_store import BiSyncStore
from ..common.log import get_logger
from .bi_sync_tasks import BiSyncRunThread
from .signals import _SyncJobSignals
from .sync_manager import SyncManager

logger = get_logger(__name__)


class BiSyncManager(SyncManager):
    """双向同步调度器（继承 SyncManager 的调度/生命周期框架）。

    - 使用独立的 BiSyncStore（bi_sync_* 表），与单向同步互不影响
    - 登录成功后（set_pan）自动触发一次「启动静默同步」：
      对所有开启「登录后立即静默同步」的启用任务立即运行，
      用于 B 机开机自动登录后静默拉取云端内容到本地
    - 定时调度、托盘「立即同步」、退出清理均与单向同步行为一致
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        # SyncManager.__init__ 会将 _store 设为 SyncStore，
        # 这里必须在 super().__init__ 之后再替换为 BiSyncStore
        self._store = BiSyncStore()

    def set_pan(self, pan):
        """绑定登录会话；登录成功时触发启动静默同步。"""
        super().set_pan(pan)
        if pan is not None:
            # 延迟到事件循环空闲时执行，避免登录流程中阻塞主线程
            QTimer.singleShot(0, self._run_startup_jobs)

    def _run_startup_jobs(self):
        """登录后静默同步：立即运行所有「登录后立即同步」的启用任务。"""
        if self._pan is None:
            return
        for job in self._store.get_jobs(enabled_only=True):
            if not bool(job.get("sync_on_startup")):
                continue
            job_id = int(job["id"])
            if job_id in self._running:
                continue
            self.run_job(job_id)
            logger.info("双向同步启动静默同步: job=%s", job.get("name"))

    def _start_thread(self, job):
        """启动双向同步线程（覆盖 SyncManager 使用 BiSyncRunThread）。"""
        job_id = int(job["id"])
        signals = _SyncJobSignals()
        thread = BiSyncRunThread(self._pan, job, signals)
        self._running[job_id] = thread

        signals.status.connect(self.jobStatusChanged.emit)
        signals.file_progress.connect(self.jobFileProgress.emit)
        signals.file_done.connect(self.jobFileDone.emit)
        signals.finished.connect(
            lambda jid, ok, summary, stats: self._on_job_finished(
                jid, ok, summary, stats, thread
            )
        )

        self._store.set_job_last_run(job_id)
        self.jobsChanged.emit()
        logger.info("双向同步任务启动: job=%s", job.get("name"))
        thread.start()
