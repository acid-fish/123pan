"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget

from src.app.common.utils import configure_resizable_header, format_db_time


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestFormatDbTime:
    def test_utc_converted_to_local(self):
        """带时区的 UTC 字符串应转换为系统本地时区显示。"""
        utc_now = datetime.now(timezone.utc)
        text = format_db_time(utc_now.isoformat())
        assert text == utc_now.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def test_utc_offset_shifted(self):
        """固定 UTC 输入，结果与本地时区偏移一致。"""
        # 独立于运行环境时区：解析后 astimezone 的结果必须携带正确偏移
        text = format_db_time("2026-09-28T01:32:23.522+00:00")
        local = datetime.fromisoformat("2026-09-28T01:32:23.522+00:00")
        local = local.astimezone()
        assert text == local.strftime("%Y-%m-%d %H:%M:%S")

    def test_empty_value(self):
        assert format_db_time("") == "-"
        assert format_db_time(None) == "-"

    def test_invalid_string_falls_back(self):
        """非 ISO 格式字符串按原样截断（与旧显示逻辑一致）。"""
        assert format_db_time("2026-09-28 01:32:23") == "2026-09-28 01:32:23"
        assert format_db_time("garbage") == "garbage"

    def test_naive_string_kept_as_is(self):
        """无时区信息的字符串不偏移，按字面值格式化。"""
        assert format_db_time("2026-09-28T09:32:23") == "2026-09-28 09:32:23"


class TestConfigureResizableHeader:
    @staticmethod
    def _make_table(width):
        table = QTableWidget(2, 3)
        table.resize(width, 200)
        table.show()
        for col, w in enumerate((100, 100, 100)):
            table.setColumnWidth(col, w)
        return table

    def test_stretch_fills_viewport_by_default(self, qapp):
        """无 right_padding 时 stretch 列吸收全部多余宽度，填满视口。"""
        table = self._make_table(800)
        configure_resizable_header(table, stretch_column=2)
        total = sum(table.columnWidth(i) for i in range(3))
        assert total >= table.viewport().width() - 10

    def test_right_padding_reserved(self, qapp):
        """right_padding=100 时右侧保留 100px 空白。"""
        table = self._make_table(800)
        configure_resizable_header(table, stretch_column=2, right_padding=100)
        total = sum(table.columnWidth(i) for i in range(3))
        gap = table.viewport().width() - total
        # _stretch 阈值 10px 内即视为满足
        assert 90 <= gap <= 110

    def test_shrink_recovers_padding_after_overshoot(self, qapp):
        """padding 模式：stretch 列过冲后重算可收缩，恢复右侧空白。"""
        table = self._make_table(800)
        stretch = configure_resizable_header(table, stretch_column=2, right_padding=100)
        vp = table.viewport().width()
        total = sum(table.columnWidth(i) for i in range(3))
        assert 90 <= vp - total <= 110  # 先拉伸到位

        # 模拟布局中间态过冲：人为加宽第 0 列 60px
        table.setColumnWidth(0, table.columnWidth(0) + 60)
        stretch()  # 重算
        total = sum(table.columnWidth(i) for i in range(3))
        assert 90 <= vp - total <= 110  # 空白恢复 100

    def test_shrink_floor_protects_content(self, qapp):
        """stretch 列不收缩到内容提示宽度以下（内容优先，出滚动条）。"""
        table = self._make_table(800)
        stretch = configure_resizable_header(table, stretch_column=2, right_padding=100)
        header = table.horizontalHeader()
        # 其他列人为超宽，可压缩的只剩 stretch 列
        table.setColumnWidth(0, 700)
        table.setColumnWidth(1, 700)
        stretch()
        assert table.columnWidth(2) >= header.sectionSizeHint(2) - 2
