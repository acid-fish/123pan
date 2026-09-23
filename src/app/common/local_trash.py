"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from pathlib import Path

from PySide6.QtCore import QFile


class LocalTrashError(OSError):
    """本地文件无法移入操作系统回收站。"""


def move_to_system_trash(path):
    """把文件移入系统回收站；失败时保留原文件并抛出异常。"""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        raise IsADirectoryError(f"仅支持将文件移入回收站: {path}")
    if not QFile.supportsMoveToTrash():
        raise LocalTrashError("当前系统或文件系统不支持回收站")

    file = QFile(str(path))
    if file.moveToTrash():
        return

    error = file.errorString() or "系统回收站操作失败"
    raise LocalTrashError(f"无法移入系统回收站: {error}")
