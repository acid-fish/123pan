"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QHeaderView


def format_db_time(value):
    """将数据库存储的 UTC 时间字符串格式化为本地时间显示。

    存储层（sync_store / bi_sync_store / transfer_store）统一使用
    datetime.now(timezone.utc).isoformat() 写入；显示前需转换为
    系统本地时区，否则东八区下会比真实时间早 8 小时。

    Args:
        value: ISO 8601 字符串（带时区后缀）；空值返回 "-"

    Returns:
        "YYYY-MM-DD HH:MM:SS"（本地时区）；解析失败时按原样截断显示
    """
    if not value:
        return "-"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text.replace("T", " ").split(".", maxsplit=1)[0]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()  # UTC -> 系统本地时区
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def format_file_size(size):
    """将字节数格式化为人类可读的文件大小字符串。

    Args:
        size: 文件大小（字节）

    Returns:
        格式化后的字符串，如 "1.5 GB"、"256 KB"
    """
    if size < 0:
        return "0 B"
    if size == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit_index = 0
    value = float(size)
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1

    # B 级别显示整数，其他级别保留一位小数（兼容旧版行为）
    rounded = round(value, 2)
    if unit_index == 0:
        return f"{int(rounded)} {units[unit_index]}"
    return f"{rounded} {units[unit_index]}"


def format_speed(bps):
    """将字节/秒格式化为可读速度字符串。

    Args:
        bps: 每秒传输字节数（<=0 时显示占位符 "--"）

    Returns:
        格式化后的速度字符串，如 "1.5 MB/s"、"256 KB/s"、"--"
    """
    if bps <= 0:
        return "--"
    return format_file_size(bps) + "/s"


def configure_resizable_header(
    table, stretch_column=0, default_widths=None, right_padding=0
):
    """配置表格表头：所有列可交互调整列宽，指定列吸收多余宽度。

    所有列默认可拖动调整宽度；表格变宽（窗口 resize）时，
    stretch_column 自动吸收多余宽度，避免右侧出现空白。
    使用该工具后无需再手动 setSectionResizeMode。

    Args:
        table: 表格控件（QTableWidget / qfluentwidgets.TableWidget）
        stretch_column: 吸收多余宽度的列号（默认 0）
        default_widths: {列号: 初始宽度}，设置部分列的初始宽度
        right_padding: 表格右侧保留的空白宽度（像素）；stretch 列只
            吸收扣除该空白后的多余宽度，默认 0 保持完全填满行为

    Returns:
        callable: stretch() 函数，可在界面 resize 时主动调用
        （内部已自动监听表格 resize，通常无需手动调用）
    """
    header = table.horizontalHeader()
    if header is None:
        return lambda: None

    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setMinimumSectionSize(40)
    if default_widths:
        for col, width in default_widths.items():
            try:
                header.resizeSection(int(col), int(width))
            except (TypeError, ValueError):
                pass

    def _stretch():
        try:
            if header is None or header.count() == 0:
                return
            total = sum(header.sectionSize(i) for i in range(header.count()))
            extra = header.viewport().width() - total - right_padding
            if extra > 10:
                header.resizeSection(
                    stretch_column, header.sectionSize(stretch_column) + extra
                )
            elif right_padding > 0 and extra < -10:
                # padding 模式下内容/布局中间态超宽时收缩 stretch 列，
                # 维持右侧空白恒定；下限为内容提示宽度，不裁剪内容
                current = header.sectionSize(stretch_column)
                floor = max(
                    header.sectionSizeHint(stretch_column),
                    header.minimumSectionSize(),
                )
                if current > floor:
                    header.resizeSection(
                        stretch_column, max(floor, current + extra)
                    )
        except RuntimeError:
            # 表格销毁过程中 header 已被删除
            pass

    class _StretchFilter(QObject):
        def eventFilter(self, _obj, event):
            if event.type() == QEvent.Type.Resize:
                # viewport/widget Resize 事件处理期间几何尚未提交，
                # 需延迟一拍待尺寸稳定后重算列宽
                QTimer.singleShot(0, _stretch)
            return False

    table.installEventFilter(_StretchFilter(table))
    # 滚动条出现/消失会改变 viewport 宽度而不触发表格自身的 Resize 事件，
    # 需同时监听 viewport 才能在布局稳定后重算列宽
    if table.viewport() is not None:
        table.viewport().installEventFilter(_StretchFilter(table))
    _stretch()
    return _stretch
