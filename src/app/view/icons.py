"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from qfluentwidgets import Theme
from qfluentwidgets.common.icon import FluentIconBase, getIconColor

# 图标缓存：避免每行/每次右键都重新解码 SVG 图标。
# 各界面（文件页/回收站/目录选择）共用同一份缓存。
_ICONS = {}


class BiSyncIcon(FluentIconBase):
    """双向同步图标：上下双向箭头（Fluent 风格，主题自适应）。

    使用官方 Fluent UI System Icons 的 Arrow Bidirectional Up Down 图标，
    与 qfluentwidgets 图标同一绘制格式（16×16 视图 + 512 网格路径）。
    资源位于 :/123pan/icons/bisync_black.svg / bisync_white.svg。
    """

    def path(self, theme=Theme.AUTO):
        return f":/123pan/icons/bisync_{getIconColor(theme)}.svg"


BISYNC_ICON = BiSyncIcon()


def icon(enum_member):
    """懒加载并缓存 FluentIcon 对应的 QIcon（线程安全由 GIL 保证）。"""
    cached = _ICONS.get(enum_member)
    if cached is None:
        cached = enum_member.icon()
        _ICONS[enum_member] = cached
    return cached
