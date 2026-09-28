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
    BiSyncProgressWindow,
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


class TestBiSyncProgressWindowPlacement:
    def _cleanup(self, qapp, *widgets):
        for widget in widgets:
            widget.hide()
            widget.deleteLater()
        qapp.processEvents()

    def test_aligned_to_main_window_bottom_centered(self, qapp):
        """主窗口可见时：底边重合、水平居中（主窗口足够宽避免钳制）。"""
        main = QWidget()
        main.setGeometry(100, 100, 600, 500)
        main.show()
        window = BiSyncProgressWindow(main_window=main)
        # offscreen 屏幕仅 800x800，放宽产品端 820x500 最小尺寸约束以便小窗测试
        window.setMinimumSize(0, 0)
        window.resize(400, 300)
        window.present()

        main_frame = main.frameGeometry()
        win_frame = window.frameGeometry()
        assert abs(win_frame.bottom() - main_frame.bottom()) <= 2
        assert abs(win_frame.center().x() - main_frame.center().x()) <= 2
        self._cleanup(qapp, window, main)

    def test_falls_back_to_screen_center_when_main_hidden(self, qapp):
        """主窗口隐藏（静默同步）时回退屏幕居中。"""
        from PySide6.QtGui import QGuiApplication

        main = QWidget()
        main.hide()
        window = BiSyncProgressWindow(main_window=main)
        window.present()

        screen = QGuiApplication.primaryScreen().availableGeometry()
        center = window.frameGeometry().center()
        assert abs(center.x() - screen.center().x()) <= 2
        assert abs(center.y() - screen.center().y()) <= 2
        self._cleanup(qapp, window, main)

    def test_falls_back_without_main_window(self, qapp):
        """无主窗口引用（无 parent 控制器）时回退屏幕居中。"""
        from PySide6.QtGui import QGuiApplication

        window = BiSyncProgressWindow()
        window.present()

        screen = QGuiApplication.primaryScreen().availableGeometry()
        center = window.frameGeometry().center()
        assert abs(center.x() - screen.center().x()) <= 2
        assert abs(center.y() - screen.center().y()) <= 2
        self._cleanup(qapp, window)

    def test_clamped_into_screen_when_main_is_small(self, qapp):
        """主窗口过小时详情窗钳制在屏幕可用区域内，完整可见。"""
        from PySide6.QtGui import QGuiApplication

        main = QWidget()
        main.setGeometry(50, 50, 300, 200)
        main.show()
        window = BiSyncProgressWindow(main_window=main)
        window.setMinimumSize(0, 0)
        window.resize(700, 600)
        window.present()

        screen = QGuiApplication.primaryScreen().availableGeometry()
        # 窗口显示后 frame 会向四周扩展几像素（平台边框），留 4px 容差
        frame = window.frameGeometry()
        assert frame.left() >= screen.left() - 4
        assert frame.top() >= screen.top() - 4
        assert frame.right() <= screen.right() + 4
        assert frame.bottom() <= screen.bottom() + 4
        self._cleanup(qapp, window, main)
