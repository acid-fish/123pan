"""Main window startup geometry tests."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtWidgets import QApplication, QWidget

from src.app.view import main_window as main_window_module


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeManager(QObject):
    jobRunEvent = Signal(object)

    def shutdown(self):
        pass


class _FakeProgressController:
    def __init__(self, _manager, _parent=None):
        pass

    def shutdown(self):
        pass


class _FakeFileInterface(QWidget):
    pass


def _create_window(monkeypatch):
    monkeypatch.setattr(main_window_module, "SyncManager", _FakeManager)
    monkeypatch.setattr(main_window_module, "BiSyncManager", _FakeManager)
    monkeypatch.setattr(
        main_window_module,
        "BiSyncProgressController",
        _FakeProgressController,
    )
    monkeypatch.setattr(main_window_module, "FileInterface", _FakeFileInterface)
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_startup_login_flow",
        lambda _self: None,
    )
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_initNavigation",
        lambda _self: None,
    )
    monkeypatch.setattr(
        main_window_module.ConfigManager,
        "get_setting",
        lambda _key, default=None: default,
    )
    return main_window_module.MainWindow()


class TestMainWindowGeometry:
    def test_first_show_uses_1200_by_800_and_centers_on_primary_screen(
        self, qapp, monkeypatch
    ):
        window = _create_window(monkeypatch)
        window.show()
        qapp.processEvents()

        screen = qapp.primaryScreen()
        assert window.width() == 1200
        assert window.height() == 800
        assert screen is not None
        delta = window.frameGeometry().center() - screen.availableGeometry().center()
        assert abs(delta.x()) <= 1, (
            f"delta={delta} pos={window.pos()} geometry={window.geometry()} "
            f"frame={window.frameGeometry()} area={screen.availableGeometry()} "
            f"screen={screen.geometry()} dpr={screen.devicePixelRatio()} "
            f"platform={qapp.platformName()}"
        )
        assert abs(delta.y()) <= 1

        window.close()
        qapp.processEvents()

    def test_second_show_preserves_user_position(self, qapp, monkeypatch):
        window = _create_window(monkeypatch)
        window.show()
        qapp.processEvents()
        custom_position = QPoint(23, 45)
        window.move(custom_position)

        window.hide()
        window.show()
        qapp.processEvents()

        assert window.pos() == custom_position
        window.close()
        qapp.processEvents()
