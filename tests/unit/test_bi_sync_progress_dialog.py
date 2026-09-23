"""Widget tests for the modeless two-way-sync details monitor."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QWidget

from src.app.common.bi_sync_events import (
    BiSyncAction,
    BiSyncEventType,
    BiSyncItemStatus,
    BiSyncPlanItem,
    BiSyncRunEvent,
    BiSyncServiceEvent,
)
from src.app.view.bi_sync_progress_dialog import (
    BiSyncItemTableModel,
    BiSyncProgressController,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeManager(QObject):
    jobRunEvent = Signal(object)


def _run_event(event, run_id="run-1", job_id=1, job_name="Sync Job"):
    return BiSyncRunEvent(
        run_id=run_id,
        job_id=job_id,
        job_name=job_name,
        event=event,
    )


def _service_event(event_type, **kwargs):
    return BiSyncServiceEvent(event_type=event_type, **kwargs)


def _plan_item(item_id="download:a.txt", name="a.txt", size=100):
    return BiSyncPlanItem(
        item_id=item_id,
        action=BiSyncAction.DOWNLOAD,
        relative_path=name,
        name=name,
        is_dir=False,
        size=size,
        modified_at=1_700_000_000,
    )


class TestBiSyncProgressWindow:
    def test_window_is_parentless_modeless_and_visible_while_main_is_hidden(
        self, qapp
    ):
        manager = _FakeManager()
        main_window = QWidget()
        main_window.hide()
        controller = BiSyncProgressController(manager, main_window)

        manager.jobRunEvent.emit(
            _run_event(_service_event(BiSyncEventType.RUN_STARTED))
        )
        qapp.processEvents()

        window = controller.window
        assert window is not None
        assert window.parentWidget() is None
        assert window.isModal() is False
        assert window.windowModality() == Qt.WindowModality.NonModal
        assert not (
            window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        )
        assert window.isVisible()
        assert not main_window.isVisible()
        controller.shutdown()
        main_window.deleteLater()
        qapp.processEvents()

    def test_plan_progress_and_completion_remain_visible(self, qapp):
        manager = _FakeManager()
        controller = BiSyncProgressController(manager)
        manager.jobRunEvent.emit(
            _run_event(_service_event(BiSyncEventType.RUN_STARTED))
        )
        manager.jobRunEvent.emit(
            _run_event(
                _service_event(
                    BiSyncEventType.PLAN_READY,
                    items=(_plan_item(),),
                )
            )
        )
        manager.jobRunEvent.emit(
            _run_event(
                _service_event(
                    BiSyncEventType.ITEM_PROGRESS,
                    item_id="download:a.txt",
                    transferred=40,
                    total=100,
                    status=BiSyncItemStatus.RUNNING,
                )
            )
        )
        manager.jobRunEvent.emit(
            _run_event(
                _service_event(
                    BiSyncEventType.ITEM_FINISHED,
                    item_id="download:a.txt",
                    status=BiSyncItemStatus.COMPLETED,
                )
            )
        )
        manager.jobRunEvent.emit(
            _run_event(
                _service_event(
                    BiSyncEventType.RUN_FINISHED,
                    status=BiSyncItemStatus.COMPLETED,
                    success=True,
                    summary="done",
                    stats={},
                )
            )
        )
        qapp.processEvents()

        window = controller.window
        page = window.pages["run-1"]
        assert page.model.rowCount() == 1
        progress_index = page.model.index(
            0, BiSyncItemTableModel.COLUMN_PROGRESS
        )
        assert page.model.data(
            progress_index, BiSyncItemTableModel.PROGRESS_ROLE
        ) == 100
        assert page.totalProgress.value() == 100
        assert page.finished is True
        assert window.isVisible()

        window.closeButton.click()
        qapp.processEvents()
        assert not window.isVisible()
        controller.shutdown()
        qapp.processEvents()

    def test_repeated_run_reuses_the_job_page(self, qapp):
        manager = _FakeManager()
        controller = BiSyncProgressController(manager)
        manager.jobRunEvent.emit(
            _run_event(_service_event(BiSyncEventType.RUN_STARTED))
        )
        manager.jobRunEvent.emit(
            _run_event(
                _service_event(
                    BiSyncEventType.PLAN_READY,
                    items=(_plan_item(),),
                )
            )
        )
        manager.jobRunEvent.emit(
            _run_event(
                _service_event(BiSyncEventType.RUN_STARTED),
                run_id="run-2",
            )
        )
        qapp.processEvents()

        window = controller.window
        assert set(window.pages) == {"run-2"}
        assert window.stack.count() == 1
        assert window.pages["run-2"].model.rowCount() == 0
        controller.shutdown()
        qapp.processEvents()

    def test_multiple_runs_share_one_window_with_separate_pages(self, qapp):
        manager = _FakeManager()
        controller = BiSyncProgressController(manager)
        manager.jobRunEvent.emit(
            _run_event(_service_event(BiSyncEventType.RUN_STARTED))
        )
        manager.jobRunEvent.emit(
            _run_event(
                _service_event(BiSyncEventType.RUN_STARTED),
                run_id="run-2",
                job_id=2,
                job_name="Second Job",
            )
        )
        qapp.processEvents()

        window = controller.window
        assert set(window.pages) == {"run-1", "run-2"}
        assert window.stack.count() == 2
        assert window.segmented.isVisible()
        controller.shutdown()
        qapp.processEvents()
