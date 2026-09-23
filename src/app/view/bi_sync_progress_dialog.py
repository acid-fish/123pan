"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    PrimaryPushButton,
    ProgressBar,
    SegmentedWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TableView,
    isDarkTheme,
    themeColor,
)

from ..common.bi_sync_events import (
    BiSyncAction,
    BiSyncEventType,
    BiSyncItemStatus,
)
from ..common.i18n import tr
from ..common.utils import format_file_size
from .file_table import format_date_text


_ACTION_TEXT = {
    BiSyncAction.CREATE_REMOTE_DIR: ("bisync.monitor.action_create_remote", "创建云端目录"),
    BiSyncAction.UPLOAD: ("bisync.monitor.action_upload", "上传"),
    BiSyncAction.CREATE_LOCAL_DIR: ("bisync.monitor.action_create_local", "创建本地目录"),
    BiSyncAction.DOWNLOAD: ("bisync.monitor.action_download", "下载"),
    BiSyncAction.CONFLICT_COPY: ("bisync.monitor.action_conflict", "保存冲突副本"),
    BiSyncAction.DELETE_REMOTE: ("bisync.monitor.action_delete_remote", "删除云端项目"),
    BiSyncAction.TRASH_LOCAL: ("bisync.monitor.action_trash_local", "移入本地回收站"),
    BiSyncAction.REMOVE_LOCAL_DIR: ("bisync.monitor.action_remove_dir", "清理本地空目录"),
    BiSyncAction.KEEP_LOCAL: ("bisync.monitor.action_keep_local", "保留本地文件"),
    BiSyncAction.KEEP_REMOTE: ("bisync.monitor.action_keep_remote", "保留云端文件"),
    BiSyncAction.TYPE_CONFLICT: ("bisync.monitor.action_type_conflict", "类型冲突"),
}

_STATUS_TEXT = {
    BiSyncItemStatus.PENDING: ("bisync.monitor.status_pending", "等待中"),
    BiSyncItemStatus.RUNNING: ("bisync.monitor.status_running", "进行中"),
    BiSyncItemStatus.COMPLETED: ("bisync.monitor.status_completed", "已完成"),
    BiSyncItemStatus.FAILED: ("bisync.monitor.status_failed", "失败"),
    BiSyncItemStatus.BLOCKED: ("bisync.monitor.status_blocked", "已阻止"),
    BiSyncItemStatus.CANCELLED: ("bisync.monitor.status_cancelled", "已取消"),
}

_PHASE_TEXT = {
    "scan_local": ("bisync.phase_scan_local", "扫描本地文件"),
    "scan_remote": ("bisync.phase_scan_remote", "获取云端列表"),
    "upload": ("bisync.phase_upload", "上传"),
    "download": ("bisync.phase_download", "下载"),
    "delete": ("bisync.phase_delete", "删除"),
}

_TRANSFER_ACTIONS = {
    BiSyncAction.UPLOAD,
    BiSyncAction.DOWNLOAD,
    BiSyncAction.CONFLICT_COPY,
}

_TERMINAL_STATUSES = {
    BiSyncItemStatus.COMPLETED,
    BiSyncItemStatus.FAILED,
    BiSyncItemStatus.BLOCKED,
    BiSyncItemStatus.CANCELLED,
}


def _translated(mapping, key):
    translation_key, fallback = mapping[key]
    return tr(translation_key, fallback)


class _BiSyncItemState:
    """一行同步计划的可变显示状态。"""

    def __init__(self, plan_item):
        self.plan = plan_item
        self.status = BiSyncItemStatus.PENDING
        self.transferred = 0
        self.total = max(0, int(plan_item.size or 0))
        self.message = ""

    @property
    def percent(self):
        if self.status in _TERMINAL_STATUSES:
            return 100
        if self.total <= 0:
            return 0
        return max(0, min(100, int(self.transferred * 100 / self.total)))

    @property
    def work_total(self):
        if self.plan.action in _TRANSFER_ACTIONS:
            return max(1, int(self.plan.size or 0))
        return 1

    @property
    def work_done(self):
        if self.status in _TERMINAL_STATUSES:
            return self.work_total
        if self.plan.action not in _TRANSFER_ACTIONS:
            return 0
        return min(self.work_total, max(0, int(self.transferred or 0)))


class BiSyncItemTableModel(QAbstractTableModel):
    """同步详情表格模型，按稳定 item_id 增量更新。"""

    COLUMN_ACTION = 0
    COLUMN_NAME = 1
    COLUMN_TYPE = 2
    COLUMN_SIZE = 3
    COLUMN_DATE = 4
    COLUMN_PROGRESS = 5
    COLUMN_STATUS = 6
    PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._row_by_id = {}
        self._plan_ready = False
        self._headers = [
            tr("bisync.monitor.col_action", "操作"),
            tr("bisync.monitor.col_name", "名称"),
            tr("bisync.monitor.col_type", "类型"),
            tr("bisync.monitor.col_size", "大小"),
            tr("bisync.monitor.col_date", "修改日期"),
            tr("bisync.monitor.col_progress", "进度"),
            tr("bisync.monitor.col_status", "状态"),
        ]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self._headers)
        ):
            return self._headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        state = self._rows[index.row()]
        plan = state.plan
        column = index.column()

        if role == self.PROGRESS_ROLE and column == self.COLUMN_PROGRESS:
            return state.percent

        if role == Qt.ItemDataRole.ToolTipRole:
            if column == self.COLUMN_ACTION:
                return _translated(_ACTION_TEXT, plan.action)
            if column == self.COLUMN_NAME:
                return plan.relative_path
            if column == self.COLUMN_STATUS and state.message:
                return state.message
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in (self.COLUMN_TYPE, self.COLUMN_SIZE, self.COLUMN_DATE,
                          self.COLUMN_PROGRESS, self.COLUMN_STATUS):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if column == self.COLUMN_ACTION:
            return _translated(_ACTION_TEXT, plan.action)
        if column == self.COLUMN_NAME:
            return plan.name
        if column == self.COLUMN_TYPE:
            return (
                tr("file.type_folder", "文件夹")
                if plan.is_dir else tr("file.type_file", "文件")
            )
        if column == self.COLUMN_SIZE:
            return "" if plan.is_dir else format_file_size(plan.size)
        if column == self.COLUMN_DATE:
            return format_date_text(plan.modified_at)
        if column == self.COLUMN_PROGRESS:
            return f"{state.percent}%"
        if column == self.COLUMN_STATUS:
            text = _translated(_STATUS_TEXT, state.status)
            return f"{text}: {state.message}" if state.message else text
        return None

    def reset(self):
        self.beginResetModel()
        self._rows = []
        self._row_by_id = {}
        self._plan_ready = False
        self.endResetModel()

    def set_plan(self, items):
        self.beginResetModel()
        self._rows = [_BiSyncItemState(item) for item in items]
        self._row_by_id = {
            state.plan.item_id: row for row, state in enumerate(self._rows)
        }
        self._plan_ready = True
        self.endResetModel()

    def apply_event(self, event):
        row = self._row_by_id.get(event.item_id)
        if row is None:
            return
        state = self._rows[row]
        if event.status is not None:
            state.status = event.status
        if event.event_type == BiSyncEventType.ITEM_PROGRESS:
            state.transferred = max(state.transferred, int(event.transferred or 0))
            state.total = max(state.total, int(event.total or 0))
        state.message = event.message
        left = self.index(row, self.COLUMN_PROGRESS)
        right = self.index(row, self.COLUMN_STATUS)
        self.dataChanged.emit(left, right)

    def finalize_unfinished(self, status):
        if not self._rows:
            return
        changed = False
        for state in self._rows:
            if state.status not in _TERMINAL_STATUSES:
                state.status = status
                changed = True
        if changed:
            self.dataChanged.emit(
                self.index(0, self.COLUMN_PROGRESS),
                self.index(len(self._rows) - 1, self.COLUMN_STATUS),
            )

    def totals(self):
        total_items = len(self._rows)
        finished_items = sum(
            1 for state in self._rows if state.status in _TERMINAL_STATUSES
        )
        total_work = sum(state.work_total for state in self._rows)
        done_work = sum(state.work_done for state in self._rows)
        total_bytes = sum(
            int(state.plan.size or 0)
            for state in self._rows
            if state.plan.action in _TRANSFER_ACTIONS
        )
        transferred_bytes = sum(
            min(int(state.plan.size or 0), max(0, int(state.transferred or 0)))
            for state in self._rows
            if state.plan.action in _TRANSFER_ACTIONS
        )
        percent = (
            int(done_work * 100 / total_work)
            if total_work
            else (100 if self._plan_ready else 0)
        )
        return {
            "total_items": total_items,
            "finished_items": finished_items,
            "total_bytes": total_bytes,
            "transferred_bytes": transferred_bytes,
            "percent": max(0, min(100, percent)),
        }


class BiSyncProgressDelegate(QStyledItemDelegate):
    """使用当前 Qt 样式绘制进度，避免为每行创建控件。"""

    def paint(self, painter, option, index):
        if index.column() != BiSyncItemTableModel.COLUMN_PROGRESS:
            super().paint(painter, option, index)
            return

        background_option = QStyleOptionViewItem(option)
        self.initStyleOption(background_option, index)
        background_option.text = ""
        super().paint(painter, background_option, index)

        progress = int(index.data(BiSyncItemTableModel.PROGRESS_ROLE) or 0)
        rect = option.rect.adjusted(8, 11, -8, -11)
        fill_width = int(rect.width() * progress / 100)
        fill_rect = rect.adjusted(0, 0, -(rect.width() - fill_width), 0)
        palette = option.palette
        text_role = (
            QPalette.ColorRole.HighlightedText
            if option.state & QStyle.StateFlag.State_Selected
            else QPalette.ColorRole.Text
        )

        painter.save()
        track_color = (
            QColor(255, 255, 255, 38)
            if isDarkTheme()
            else QColor(0, 0, 0, 28)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, 3, 3)
        if fill_width > 0:
            painter.setBrush(themeColor())
            painter.drawRoundedRect(fill_rect, 3, 3)
        painter.setPen(palette.color(text_role))
        painter.drawText(
            option.rect,
            Qt.AlignmentFlag.AlignCenter,
            f"{progress}%",
        )
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(max(120, size.width()), max(38, size.height()))


class BiSyncRunPage(QWidget):
    """一个同步运行的计划和实时状态页。"""

    def __init__(self, job_name, parent=None):
        super().__init__(parent)
        self._job_name = job_name
        self._finished = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.nameLabel = StrongBodyLabel(
            job_name or tr("bisync.monitor.unnamed_job", "未命名同步任务"), self
        )
        self.phaseLabel = CaptionLabel(
            tr("bisync.monitor.preparing", "准备同步"), self
        )
        header.addWidget(self.nameLabel)
        header.addStretch(1)
        header.addWidget(self.phaseLabel)
        layout.addLayout(header)

        self.model = BiSyncItemTableModel(self)
        self.table = TableView(self)
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(
            BiSyncItemTableModel.COLUMN_PROGRESS,
            BiSyncProgressDelegate(self.table),
        )
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(48)
        for column, width in {
            0: 105,
            2: 60,
            3: 90,
            4: 125,
            5: 120,
            6: 170,
        }.items():
            header.resizeSection(column, width)
        header.setSectionResizeMode(
            BiSyncItemTableModel.COLUMN_NAME,
            QHeaderView.ResizeMode.Stretch,
        )
        layout.addWidget(self.table, 1)

        self.totalProgress = ProgressBar(self)
        self.totalProgress.setRange(0, 100)
        self.totalProgress.setValue(0)
        self.totalProgress.setTextVisible(False)
        self.totalProgress.setFixedHeight(7)
        layout.addWidget(self.totalProgress)

        footer = QHBoxLayout()
        self.itemSummaryLabel = BodyLabel("", self)
        self.byteSummaryLabel = CaptionLabel("", self)
        footer.addWidget(self.itemSummaryLabel)
        footer.addStretch(1)
        footer.addWidget(self.byteSummaryLabel)
        layout.addLayout(footer)
        self.__refresh_summary()

    @property
    def finished(self):
        return self._finished

    def reset(self, job_name):
        self._job_name = job_name
        self._finished = False
        self.nameLabel.setText(
            job_name or tr("bisync.monitor.unnamed_job", "未命名同步任务")
        )
        self.phaseLabel.setText(
            tr("bisync.monitor.preparing", "准备同步")
        )
        self.model.reset()
        self.__refresh_summary()

    def handle_event(self, event):
        if event.event_type == BiSyncEventType.PHASE_CHANGED:
            value = _PHASE_TEXT.get(event.phase)
            self.phaseLabel.setText(
                tr(value[0], value[1]) if value else (event.phase or "")
            )
        elif event.event_type == BiSyncEventType.PLAN_READY:
            self.model.set_plan(event.items)
            if not event.items:
                self.phaseLabel.setText(
                    tr("bisync.monitor.up_to_date", "无需同步")
                )
        elif event.event_type in (
            BiSyncEventType.ITEM_STARTED,
            BiSyncEventType.ITEM_PROGRESS,
            BiSyncEventType.ITEM_FINISHED,
        ):
            self.model.apply_event(event)
        elif event.event_type == BiSyncEventType.RUN_FINISHED:
            self._finished = True
            final_status = event.status or (
                BiSyncItemStatus.COMPLETED
                if event.success else BiSyncItemStatus.FAILED
            )
            self.model.finalize_unfinished(final_status)
            self.phaseLabel.setText(
                event.summary
                or tr("bisync.monitor.finished", "同步已结束")
            )
        self.__refresh_summary()

    def __refresh_summary(self):
        totals = self.model.totals()
        self.totalProgress.setValue(totals["percent"])
        self.itemSummaryLabel.setText(
            tr("bisync.monitor.item_summary", "已处理 {}/{} 项").format(
                totals["finished_items"], totals["total_items"]
            )
        )
        self.byteSummaryLabel.setText(
            tr("bisync.monitor.byte_summary", "传输 {} / {}").format(
                format_file_size(totals["transferred_bytes"]),
                format_file_size(totals["total_bytes"]),
            )
        )


class BiSyncProgressWindow(QDialog):
    """可移动、非模态、不置顶的双向同步详情窗口。"""

    def __init__(self):
        super().__init__(None)
        self._pages = {}
        self._pages_by_job = {}
        self._pages_by_route = {}
        self._current_run_by_job = {}
        self._was_shown = False
        self._shutting_down = False

        self.setObjectName("biSyncProgressWindow")
        self.setWindowTitle(tr("bisync.monitor.title", "双向同步详情"))
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(1040, 640)
        self.setMinimumSize(820, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = SubtitleLabel(tr("bisync.monitor.title", "双向同步详情"), self)
        self.segmented = SegmentedWidget(self)
        self.segmented.hide()
        title_row.addWidget(title)
        title_row.addSpacing(16)
        title_row.addWidget(self.segmented, 1)
        layout.addLayout(title_row)

        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.closeButton = PrimaryPushButton(
            tr("bisync.monitor.close", "关闭"), self
        )
        self.closeButton.clicked.connect(self.hide)
        button_row.addWidget(self.closeButton)
        layout.addLayout(button_row)

        self.segmented.currentItemChanged.connect(self.__show_route)

    @property
    def pages(self):
        return dict(self._pages)

    def handle_event(self, run_event):
        event_type = run_event.event.event_type
        current_run = self._current_run_by_job.get(run_event.job_id)
        if (
            event_type != BiSyncEventType.RUN_STARTED
            and current_run is not None
            and current_run != run_event.run_id
        ):
            return

        if event_type == BiSyncEventType.RUN_STARTED:
            page = self.__start_run(run_event)
        else:
            page = self._pages.get(run_event.run_id)
            if page is None:
                page = self.__start_run(run_event)
        page.handle_event(run_event.event)
        if event_type == BiSyncEventType.RUN_STARTED:
            route_key = f"job:{run_event.job_id}"
            self.segmented.setCurrentItem(route_key)
            self.stack.setCurrentWidget(page)
            self.present()

    def present(self):
        if not self._was_shown:
            self.__center_on_current_screen()
            self._was_shown = True
        self.show()
        self.raise_()

    def shutdown(self):
        self._shutting_down = True
        self.close()
        self.deleteLater()

    def closeEvent(self, event):
        if self._shutting_down:
            event.accept()
            return
        self.hide()
        event.ignore()

    def __start_run(self, run_event):
        page = self._pages_by_job.get(run_event.job_id)
        if page is None:
            page = BiSyncRunPage(run_event.job_name, self.stack)
            route_key = f"job:{run_event.job_id}"
            self._pages_by_job[run_event.job_id] = page
            self._pages_by_route[route_key] = page
            self.stack.addWidget(page)
            self.segmented.addItem(
                routeKey=route_key,
                icon=FIF.SYNC.icon(),
                text=(
                    run_event.job_name
                    or tr("bisync.monitor.unnamed_job", "未命名任务")
                ),
            )
        else:
            page.reset(run_event.job_name)
            for old_run_id, old_page in tuple(self._pages.items()):
                if old_page is page:
                    self._pages.pop(old_run_id, None)

        self._pages[run_event.run_id] = page
        self._current_run_by_job[run_event.job_id] = run_event.run_id
        self.segmented.setVisible(len(self._pages_by_job) > 1)
        return page

    def __show_route(self, route_key):
        page = self._pages_by_route.get(route_key)
        if page is not None:
            self.stack.setCurrentWidget(page)

    def __center_on_current_screen(self):
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(geometry.center())
        self.move(frame.topLeft())


class BiSyncProgressController(QObject):
    """合并高频进度事件并维护独立详情窗口的生命周期。"""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._window = None
        self._pending_progress = {}
        self._flushTimer = QTimer(self)
        self._flushTimer.setSingleShot(True)
        self._flushTimer.setInterval(100)
        self._flushTimer.timeout.connect(self.__flush_progress)
        self._manager.jobRunEvent.connect(self.__on_run_event)

    @property
    def window(self):
        return self._window

    def shutdown(self):
        self._flushTimer.stop()
        self.__flush_progress()
        try:
            self._manager.jobRunEvent.disconnect(self.__on_run_event)
        except (RuntimeError, TypeError):
            pass
        if self._window is not None:
            self._window.shutdown()
            self._window = None

    def __on_run_event(self, run_event):
        event = run_event.event
        if event.event_type == BiSyncEventType.ITEM_PROGRESS:
            self._pending_progress[(run_event.run_id, event.item_id)] = run_event
            if not self._flushTimer.isActive():
                self._flushTimer.start()
            return

        self.__flush_progress()
        window = self.__ensure_window()
        window.handle_event(run_event)

    def __flush_progress(self):
        if not self._pending_progress:
            return
        window = self.__ensure_window()
        events = tuple(self._pending_progress.values())
        self._pending_progress.clear()
        for run_event in events:
            window.handle_event(run_event)

    def __ensure_window(self):
        if self._window is None:
            self._window = BiSyncProgressWindow()
        return self._window
