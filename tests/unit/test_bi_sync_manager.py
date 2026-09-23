"""Tests for two-way-sync run-event transport."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from src.app.common.bi_sync_events import (
    BiSyncEventType,
    BiSyncRunEvent,
    BiSyncServiceEvent,
)
from src.app.tasks.bi_sync_manager import BiSyncManager
from src.app.tasks.bi_sync_tasks import BiSyncRunThread
from src.app.tasks.signals import _SyncJobSignals


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakePan:
    def __init__(self):
        self._session = object()
        self.user_name = "tester"

    def login(self):
        return 200


class _FakeThread(QObject):
    runEvent = Signal(object)

    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return False


class TestBiSyncManagerEvents:
    def test_manager_forwards_thread_run_events(self, tmp_db, qapp, mocker):
        fake_thread = _FakeThread()
        mocker.patch(
            "src.app.tasks.bi_sync_manager.BiSyncRunThread",
            return_value=fake_thread,
        )
        manager = BiSyncManager()
        manager._pan = _FakePan()
        received = []
        manager.jobRunEvent.connect(received.append)
        job = {
            "id": 1,
            "name": "job",
            "local_path": "/tmp/local",
            "remote_dir_id": 0,
        }

        manager._start_thread(job)
        payload = object()
        fake_thread.runEvent.emit(payload)
        qapp.processEvents()

        assert fake_thread.started is True
        assert received == [payload]
        manager.shutdown()


class TestBiSyncRunThreadEvents:
    def test_thread_wraps_service_events_with_run_identity(
        self, tmp_db, qapp, mocker
    ):
        captured = {}

        class _FakeService:
            def __init__(self, _session, _account_name, local_trash=None):
                captured["local_trash"] = local_trash

            def run_bi_sync(self, _job, **kwargs):
                kwargs["event_callback"](
                    BiSyncServiceEvent(event_type=BiSyncEventType.PLAN_READY)
                )
                return True, {
                    "added": 0,
                    "updated": 0,
                    "downloaded": 0,
                    "conflicts": 0,
                    "deleted_remote": 0,
                    "deleted_local": 0,
                    "failed": 0,
                    "skipped": 0,
                }

        mocker.patch(
            "src.app.tasks.bi_sync_tasks.BiSyncService", _FakeService
        )
        signals = _SyncJobSignals()
        job = {"id": 7, "name": "run-events"}
        thread = BiSyncRunThread(_FakePan(), job, signals)
        received = []
        thread.runEvent.connect(received.append)

        thread.run()
        qapp.processEvents()

        assert [item.event.event_type for item in received] == [
            BiSyncEventType.RUN_STARTED,
            BiSyncEventType.PLAN_READY,
            BiSyncEventType.RUN_FINISHED,
        ]
        assert all(isinstance(item, BiSyncRunEvent) for item in received)
        assert {item.run_id for item in received} == {thread._run_id}
        assert {item.job_id for item in received} == {7}
        assert captured["local_trash"] is not None

    def test_skipped_deletions_are_not_reported_as_up_to_date(self):
        stats = {
            "added": 0,
            "updated": 0,
            "downloaded": 0,
            "conflicts": 0,
            "deleted_remote": 0,
            "deleted_local": 0,
            "failed": 0,
            "skipped": 2,
        }

        summary = BiSyncRunThread._build_summary(stats, cancelled=False)

        assert "2" in summary
        assert "已是最新" not in summary

    def test_each_thread_uses_a_distinct_run_id(self, qapp):
        signals_a = _SyncJobSignals()
        signals_b = _SyncJobSignals()
        first = BiSyncRunThread(_FakePan(), {"id": 1}, signals_a)
        second = BiSyncRunThread(_FakePan(), {"id": 1}, signals_b)

        assert first._run_id != second._run_id
