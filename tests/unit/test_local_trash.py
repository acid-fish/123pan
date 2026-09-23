"""Tests for the system Recycle Bin adapter used by two-way sync."""

import pytest

from src.app.common import local_trash


class _FakeQFile:
    supported = True
    succeeds = True
    seen_path = None

    def __init__(self, path):
        type(self).seen_path = path

    @staticmethod
    def supportsMoveToTrash():
        return _FakeQFile.supported

    def moveToTrash(self):
        return type(self).succeeds

    def errorString(self):
        return "trash operation failed"


def test_move_to_system_trash_uses_qfile(monkeypatch, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("data", encoding="utf-8")
    _FakeQFile.supported = True
    _FakeQFile.succeeds = True
    _FakeQFile.seen_path = None
    monkeypatch.setattr(local_trash, "QFile", _FakeQFile)

    local_trash.move_to_system_trash(target)

    assert _FakeQFile.seen_path == str(target)


def test_move_to_system_trash_never_falls_back_to_delete(monkeypatch, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("data", encoding="utf-8")
    _FakeQFile.supported = True
    _FakeQFile.succeeds = False
    monkeypatch.setattr(local_trash, "QFile", _FakeQFile)

    with pytest.raises(local_trash.LocalTrashError, match="trash operation failed"):
        local_trash.move_to_system_trash(target)

    assert target.exists()


def test_move_to_system_trash_rejects_real_directories(tmp_path):
    directory = tmp_path / "folder"
    directory.mkdir()

    with pytest.raises(IsADirectoryError):
        local_trash.move_to_system_trash(directory)


def test_move_to_system_trash_treats_missing_path_as_done(tmp_path):
    local_trash.move_to_system_trash(tmp_path / "missing.txt")
