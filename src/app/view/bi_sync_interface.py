"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
    QTableWidgetItem,
)

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    TableWidget,
    TitleLabel,
    ToolButton,
)

from ..common.bi_sync_store import (
    CONFLICT_KEEP_BOTH,
    CONFLICT_LOCAL_WINS,
    CONFLICT_REMOTE_WINS,
)
from ..common.i18n import tr
from ..common.log import get_logger
from ..common.style_sheet import StyleSheet
from ..common.utils import configure_resizable_header
from .folder_select_dialog import FolderSelectDialog
from .sync_interface import _SYNC_INTERVALS, _interval_index

logger = get_logger(__name__)

# 冲突处理策略选项：(策略值, 显示文本)
_CONFLICT_POLICIES = [
    (CONFLICT_KEEP_BOTH, tr("bisync.conflict_keep_both", "保留双方副本")),
    (CONFLICT_LOCAL_WINS, tr("bisync.conflict_local_wins", "以本地为准")),
    (CONFLICT_REMOTE_WINS, tr("bisync.conflict_remote_wins", "以云端为准")),
]


def _conflict_index(policy):
    """按策略值查找冲突下拉索引。"""
    for i, (val, _) in enumerate(_CONFLICT_POLICIES):
        if val == policy:
            return i
    return 0


class BiSyncJobDialog(QDialog):
    """添加/编辑双向同步任务对话框。"""

    def __init__(self, pan, job=None, parent=None):
        super().__init__(parent)
        self._pan = pan
        self._job = job  # dict 或 None（新增）
        self._remote_dir_id = int(job["remote_dir_id"]) if job else 0
        self._remote_dir_name = (job or {}).get("remote_dir_name", "")

        self.setWindowTitle(
            tr("bisync.edit_job", "编辑双向同步任务")
            if job else tr("bisync.add_job", "添加双向同步任务")
        )
        self.setMinimumSize(480, 560)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(14)

        title = TitleLabel(
            tr("bisync.edit_job", "编辑双向同步任务")
            if job else tr("bisync.add_job", "添加双向同步任务")
        )
        layout.addWidget(title)

        # 名称
        self.nameEdit = LineEdit(self)
        self.nameEdit.setPlaceholderText(
            tr("bisync.job_name_hint", "任务名称（可选）")
        )
        self.nameEdit.setText((job or {}).get("name", ""))
        layout.addWidget(QLabel(tr("bisync.job_name", "任务名称")))
        layout.addWidget(self.nameEdit)

        # 本地路径
        layout.addWidget(QLabel(tr("bisync.local_path", "本地文件夹")))
        local_row = QHBoxLayout()
        self.localEdit = LineEdit(self)
        self.localEdit.setPlaceholderText(
            tr("bisync.local_path_hint", "选择要与云盘双向同步的本地文件夹")
        )
        self.localEdit.setText((job or {}).get("local_path", ""))
        browseBtn = PushButton(FIF.FOLDER, tr("bisync.browse", "浏览..."))
        browseBtn.clicked.connect(self._on_browse_local)
        local_row.addWidget(self.localEdit, 1)
        local_row.addWidget(browseBtn, 0)
        layout.addLayout(local_row)

        # 云端目录
        layout.addWidget(QLabel(tr("bisync.remote_dir", "云端目录")))
        remote_row = QHBoxLayout()
        self.remoteEdit = LineEdit(self)
        self.remoteEdit.setReadOnly(True)
        self.remoteEdit.setPlaceholderText(
            tr("bisync.remote_dir_hint", "选择云端目录（默认根目录）")
        )
        if self._remote_dir_id:
            self.remoteEdit.setText(self._remote_dir_name or f"#{self._remote_dir_id}")
        pickBtn = PushButton(FIF.FOLDER_ADD, tr("bisync.pick_remote", "选择目录"))
        pickBtn.clicked.connect(self._on_pick_remote)
        remote_row.addWidget(self.remoteEdit, 1)
        remote_row.addWidget(pickBtn, 0)
        layout.addLayout(remote_row)

        # 同步间隔
        layout.addWidget(QLabel(tr("bisync.interval", "自动同步频率")))
        self.intervalCombo = ComboBox(self)
        for _, text in _SYNC_INTERVALS:
            self.intervalCombo.addItem(text)
        self.intervalCombo.setCurrentIndex(
            _interval_index(int((job or {}).get("interval_seconds") or 0))
        )
        self.intervalCombo.setToolTip(
            tr(
                "bisync.interval_tip",
                "设置后台自动同步频率；选择「手动」则仅在点击同步时运行",
            )
        )
        layout.addWidget(self.intervalCombo)

        # 删除云端多余文件
        self.deleteRemoteCheck = CheckBox(
            tr("bisync.delete_remote", "本地删除时同步删除云端文件"), self
        )
        self.deleteRemoteCheck.setChecked(bool((job or {}).get("delete_remote")))
        self.deleteRemoteCheck.setToolTip(
            tr(
                "bisync.delete_remote_tip",
                "启用后，本地已删除的文件会在下次同步时从云端删除（慎用）",
            )
        )
        layout.addWidget(self.deleteRemoteCheck)

        # 删除本地多余文件
        self.deleteLocalCheck = CheckBox(
            tr(
                "bisync.delete_local",
                "云端删除时将本地文件移入系统回收站",
            ),
            self,
        )
        self.deleteLocalCheck.setChecked(bool((job or {}).get("delete_local")))
        self.deleteLocalCheck.setToolTip(
            tr(
                "bisync.delete_local_tip",
                "启用后，云端已删除的文件会在下次同步时移入系统回收站；"
                "空目录仅在确认为空时清理。回收站不可用时同步会报错，"
                "不会永久删除文件。",
            )
        )
        layout.addWidget(self.deleteLocalCheck)

        # 冲突处理
        layout.addWidget(QLabel(tr("bisync.conflict", "冲突处理")))
        self.conflictCombo = ComboBox(self)
        for _, text in _CONFLICT_POLICIES:
            self.conflictCombo.addItem(text)
        self.conflictCombo.setCurrentIndex(
            _conflict_index((job or {}).get("conflict_policy"))
        )
        self.conflictCombo.setToolTip(
            tr(
                "bisync.conflict_tip",
                "本地与云端同时修改同一文件时如何处理；"
                "「保留双方副本」会保留本地版本并把云端版本另存为冲突副本文件",
            )
        )
        layout.addWidget(self.conflictCombo)

        # 登录后立即静默同步
        self.syncOnStartupCheck = CheckBox(
            tr("bisync.sync_on_startup", "登录后立即静默同步"), self
        )
        self.syncOnStartupCheck.setChecked(
            bool((job or {}).get("sync_on_startup", 1))
        )
        self.syncOnStartupCheck.setToolTip(
            tr(
                "bisync.sync_on_startup_tip",
                "启用后，软件登录成功即自动同步一次；主界面保持隐藏，"
                "但会显示可操作的同步详情窗口。",
            )
        )
        layout.addWidget(self.syncOnStartupCheck)

        layout.addStretch(1)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancelBtn = PushButton(tr("dialog.cancel", "取消"))
        cancelBtn.clicked.connect(self.reject)
        okBtn = PrimaryPushButton(tr("dialog.ok", "确定"))
        okBtn.clicked.connect(self._on_ok)
        btn_row.addWidget(cancelBtn)
        btn_row.addWidget(okBtn)
        layout.addLayout(btn_row)

    def _on_browse_local(self):
        folder = QFileDialog.getExistingDirectory(
            self, tr("bisync.pick_local", "选择本地文件夹"), str(Path.home())
        )
        if folder:
            self.localEdit.setText(folder)

    def _on_pick_remote(self):
        if self._pan is None:
            InfoBar.warning(
                title=tr("bisync.msg_no_login", "未登录"),
                content=tr("bisync.msg_no_login_desc", "请先登录后再选择云端目录"),
                parent=self,
            )
            return
        dialog = FolderSelectDialog(self._pan, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dir_id = dialog.selected_dir_id()
        if dir_id is None:
            return
        self._remote_dir_id = int(dir_id)
        self._remote_dir_name = dialog.selected_dir_name()
        self.remoteEdit.setText(self._remote_dir_name)

    def _on_ok(self):
        local = self.localEdit.text().strip()
        if not local or not Path(local).is_dir():
            InfoBar.warning(
                title=tr("bisync.msg_input_error", "输入错误"),
                content=tr("bisync.msg_local_invalid", "请选择有效的本地文件夹"),
                parent=self,
            )
            return
        self.accept()

    # ---- 结果读取 ----

    def values(self):
        """返回表单数据 dict（供 BiSyncManager.add_job / update_job 使用）。"""
        name = self.nameEdit.text().strip()
        if not name:
            name = Path(self.localEdit.text().strip()).name
        interval = _SYNC_INTERVALS[self.intervalCombo.currentIndex()][0]
        policy = _CONFLICT_POLICIES[self.conflictCombo.currentIndex()][0]
        return {
            "name": name,
            "local_path": self.localEdit.text().strip(),
            "remote_dir_id": self._remote_dir_id,
            "remote_dir_name": self._remote_dir_name,
            "interval_seconds": interval,
            "delete_remote": self.deleteRemoteCheck.isChecked(),
            "delete_local": self.deleteLocalCheck.isChecked(),
            "conflict_policy": policy,
            "sync_on_startup": self.syncOnStartupCheck.isChecked(),
        }


class BiSyncInterface(QWidget):
    """双向同步页面。

    视图层：展示任务列表与运行历史，操作均委托给 BiSyncManager。
    界面结构与现有「同步」页保持一致，仅新增下载/冲突相关展示。
    """

    def __init__(self, sync_manager, pan=None, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("BiSyncInterface")

        self._manager = sync_manager
        self._pan = pan
        # job_id -> 当前状态文本（运行中时显示进度）
        self._job_status = {}
        # job_id -> 当前阶段文本（上传/下载/删除），用于文件级进度文案
        self._job_phase = {}

        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(24, 20, 24, 24)
        self.mainLayout.setSpacing(12)

        self.__createTopBar()
        self.__createContent()
        self.__initWidget()

    def set_pan(self, pan):
        """设置 Pan123 实例（登录流程调用）。"""
        self._pan = pan

    # ---- UI 构建 ----

    def __createTopBar(self):
        self.topBarFrame = QFrame(self)
        self.topBarFrame.setObjectName("frame")
        self.topBarLayout = QHBoxLayout(self.topBarFrame)
        self.topBarLayout.setContentsMargins(12, 10, 12, 10)
        self.topBarLayout.setSpacing(8)

        self.titleLabel = QLabel(tr("bisync.title", "双向同步"), self.topBarFrame)
        self.segmented = SegmentedWidget(self.topBarFrame)
        self.segmented.addItem(
            routeKey="jobs",
            icon=FIF.SYNC.icon(),
            text=tr("bisync.tab_jobs", "同步任务"),
        )
        self.segmented.addItem(
            routeKey="history",
            icon=FIF.HISTORY.icon(),
            text=tr("bisync.tab_history", "运行历史"),
        )
        self.segmented.setCurrentItem("jobs")

        self.addButton = PushButton(
            FIF.ADD.icon(), tr("bisync.add_job", "添加同步"), self.topBarFrame
        )
        self.runAllButton = PushButton(
            FIF.PLAY.icon(), tr("bisync.run_all", "立即同步"), self.topBarFrame
        )

        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addWidget(self.segmented)
        self.topBarLayout.addStretch(1)
        self.topBarLayout.addWidget(self.runAllButton, 0)
        self.topBarLayout.addWidget(self.addButton, 0)

        self.mainLayout.addWidget(self.topBarFrame, 0)

    def __createContent(self):
        # ---- 任务表格 ----
        self.jobsFrame = QFrame(self)
        self.jobsFrame.setObjectName("frame")
        self.jobsLayout = QVBoxLayout(self.jobsFrame)
        self.jobsLayout.setContentsMargins(0, 8, 0, 0)
        self.jobsLayout.setSpacing(0)

        self.jobsTable = TableWidget(self.jobsFrame)
        self.jobsTable.setAlternatingRowColors(True)
        self.jobsTable.setColumnCount(7)
        self.jobsTable.setHorizontalHeaderLabels([
            tr("bisync.col_status", "状态"),
            tr("bisync.col_name", "任务名称"),
            tr("bisync.col_local", "本地路径"),
            tr("bisync.col_remote", "云端目录"),
            tr("bisync.col_interval", "频率"),
            tr("bisync.col_last_run", "上次同步"),
            tr("bisync.col_action", "操作"),
        ])
        self.jobsTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.jobsTable.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.jobsTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        configure_resizable_header(
            self.jobsTable,
            stretch_column=2,
            default_widths={0: 80, 1: 180, 2: 300, 3: 180, 4: 100, 5: 160},
        )
        self.jobsTable.setColumnWidth(6, 200)
        self.jobsLayout.addWidget(self.jobsTable)

        # ---- 历史表格 ----
        self.historyFrame = QFrame(self)
        self.historyFrame.setObjectName("frame")
        self.historyLayout = QVBoxLayout(self.historyFrame)
        self.historyLayout.setContentsMargins(0, 8, 0, 0)
        self.historyLayout.setSpacing(0)

        history_header = QHBoxLayout()
        self.clearHistoryButton = PushButton(
            FIF.DELETE.icon(),
            tr("transfer.clear_history", "清空历史"),
            self.historyFrame,
        )
        self.clearHistoryButton.clicked.connect(self.__clear_history)
        history_header.addStretch(1)
        history_header.addWidget(self.clearHistoryButton)
        self.historyLayout.addLayout(history_header)

        self.historyTable = TableWidget(self.historyFrame)
        self.historyTable.setAlternatingRowColors(True)
        self.historyTable.setColumnCount(8)
        self.historyTable.setHorizontalHeaderLabels([
            tr("bisync.h_col_job", "任务"),
            tr("bisync.h_col_status", "结果"),
            tr("bisync.h_col_added", "新增"),
            tr("bisync.h_col_updated", "更新"),
            tr("bisync.h_col_downloaded", "下载"),
            tr("bisync.h_col_deleted", "删除"),
            tr("bisync.h_col_conflicts", "冲突"),
            tr("bisync.h_col_time", "时间"),
        ])
        self.historyTable.setBorderRadius(8)
        self.historyTable.setBorderVisible(True)
        configure_resizable_header(
            self.historyTable,
            stretch_column=0,
            default_widths={0: 200, 1: 80, 2: 60, 3: 60, 4: 60, 5: 60, 6: 60, 7: 150},
        )
        self.historyLayout.addWidget(self.historyTable)

        self.historyFrame.hide()
        self.mainLayout.addWidget(self.jobsFrame)
        self.mainLayout.addWidget(self.historyFrame)

    def __initWidget(self):
        StyleSheet.VIEW_INTERFACE.apply(self)
        self.segmented.currentItemChanged.connect(self.__on_segment_changed)
        self.addButton.clicked.connect(self.__add_job)
        self.runAllButton.clicked.connect(self.__run_all)
        self.clearHistoryButton.clicked.connect(self.__clear_history)

        # 连接 BiSyncManager 信号
        self._manager.jobsChanged.connect(self.__refresh_jobs)
        self._manager.jobStatusChanged.connect(self.__on_status_changed)
        self._manager.jobFileProgress.connect(self.__on_file_progress)
        self._manager.jobFinished.connect(self.__on_job_finished)
        self._manager.jobFileDone.connect(self.__on_file_done)

        self.__refresh_jobs()

    # ---- 事件处理 ----

    def showEvent(self, event):
        super().showEvent(event)
        self.__refresh_jobs()
        if self.segmented.currentRouteKey() == "history":
            self.__refresh_history()

    def __on_segment_changed(self, route_key):
        if route_key == "jobs":
            self.jobsFrame.show()
            self.historyFrame.hide()
            self.__refresh_jobs()
        else:
            self.jobsFrame.hide()
            self.historyFrame.show()
            self.__refresh_history()

    def __add_job(self):
        if self._pan is None:
            InfoBar.warning(
                title=tr("bisync.msg_no_login", "未登录"),
                content=tr("bisync.msg_no_login_desc", "请先登录后再添加同步任务"),
                parent=self,
            )
            return
        dialog = BiSyncJobDialog(self._pan, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._manager.add_job(**dialog.values())
        InfoBar.success(
            title=tr("bisync.msg_added", "已添加"),
            content=tr("bisync.msg_added_desc", "双向同步任务已创建"),
            parent=self,
        )

    def __edit_job(self, job):
        dialog = BiSyncJobDialog(self._pan, job=job, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._manager.update_job(
            int(job["id"]),
            name=values["name"],
            local_path=values["local_path"],
            remote_dir_id=values["remote_dir_id"],
            remote_dir_name=values["remote_dir_name"],
            interval_seconds=values["interval_seconds"],
            delete_remote=values["delete_remote"],
            delete_local=values["delete_local"],
            conflict_policy=values["conflict_policy"],
            sync_on_startup=values["sync_on_startup"],
        )
        InfoBar.success(
            title=tr("bisync.msg_updated", "已保存"),
            content=tr("bisync.msg_updated_desc", "双向同步任务配置已更新"),
            parent=self,
        )

    def __delete_job(self, job):
        from qfluentwidgets import MessageBox

        box = MessageBox(
            tr("bisync.msg_delete_title", "删除双向同步任务"),
            tr("bisync.msg_delete_confirm", "确定删除双向同步任务「{}」吗？").format(
                job.get("name", "")
            ),
            self,
        )
        if box.exec():
            self._manager.delete_job(int(job["id"]))

    def __toggle_job(self, job):
        enabled = not bool(job.get("enabled"))
        self._manager.set_job_enabled(int(job["id"]), enabled)

    def __run_job(self, job):
        if self._pan is None:
            return
        self._manager.run_job(int(job["id"]))

    def __cancel_job(self, job):
        self._manager.cancel_job(int(job["id"]))

    def __run_all(self):
        if self._pan is None:
            InfoBar.warning(
                title=tr("bisync.msg_no_login", "未登录"),
                content=tr("bisync.msg_no_login_desc", "请先登录"),
                parent=self,
            )
            return
        self._manager.run_all_enabled()
        InfoBar.info(
            title=tr("bisync.msg_run_all", "开始同步"),
            content=tr(
                "bisync.msg_run_all_desc", "已启动全部启用中的双向同步任务"
            ),
            duration=3000,
            parent=self,
        )

    # ---- BiSyncManager 信号回调 ----

    def __on_status_changed(self, job_id, text):
        self._job_phase[job_id] = text
        self._job_status[job_id] = text
        self.__refresh_jobs()

    def __on_file_progress(self, job_id, rel_path, current, total):
        phase = self._job_phase.get(job_id, "")
        self._job_status[job_id] = tr(
            "bisync.status_progress", "{phase}中 {rel} ({cur}/{total})"
        ).format(phase=phase, rel=rel_path, cur=current, total=total)
        self.__refresh_jobs()

    def __on_file_done(self, job_id, rel_path, ok, error):
        if ok:
            self._job_status[job_id] = tr("bisync.status_idle", "空闲")
        else:
            self._job_status[job_id] = tr("bisync.status_error", "错误: {}").format(
                error
            )
        self.__refresh_jobs()

    def __on_job_finished(self, job_id, ok, summary, stats):
        self._job_status.pop(job_id, None)
        self._job_phase.pop(job_id, None)
        self.__refresh_jobs()
        self.__refresh_history()
        if ok:
            InfoBar.success(
                title=tr("bisync.msg_run_done", "同步完成"),
                content=summary,
                duration=3000,
                parent=self,
            )
        else:
            InfoBar.error(
                title=tr("bisync.msg_run_failed", "同步失败"),
                content=summary,
                duration=3000,
                parent=self,
            )

    def __clear_history(self):
        self._manager.clear_history()
        self.__refresh_history()

    # ---- 表格刷新 ----

    def __refresh_jobs(self):
        jobs = self._manager.get_jobs()
        running = self._manager.running_ids()
        self.jobsTable.setRowCount(len(jobs))
        self.jobsTable.setUpdatesEnabled(False)
        try:
            for row, job in enumerate(jobs):
                job_id = int(job["id"])
                is_running = job_id in running

                status_text = self._job_status.get(
                    job_id,
                    tr("bisync.status_running", "运行中")
                    if is_running
                    else (
                        tr("bisync.status_disabled", "已停用")
                        if not bool(job.get("enabled"))
                        else tr("bisync.status_idle", "空闲")
                    ),
                )
                status_item = QTableWidgetItem(status_text)
                if is_running:
                    status_item.setForeground(Qt.GlobalColor.darkGreen)
                elif not bool(job.get("enabled")):
                    status_item.setForeground(Qt.GlobalColor.gray)
                self.jobsTable.setItem(row, 0, status_item)

                self.jobsTable.setItem(row, 1, QTableWidgetItem(job.get("name", "")))
                self.jobsTable.setItem(
                    row, 2, QTableWidgetItem(job.get("local_path", ""))
                )

                remote_name = job.get("remote_dir_name", "")
                remote_text = remote_name or (
                    tr("file.root_dir", "根目录")
                    if int(job.get("remote_dir_id") or 0) == 0
                    else f"#{job.get('remote_dir_id')}"
                )
                self.jobsTable.setItem(row, 3, QTableWidgetItem(remote_text))

                interval_text = next(
                    (
                        t for v, t in _SYNC_INTERVALS
                        if v == int(job.get("interval_seconds") or 0)
                    ),
                    tr("bisync.interval_manual", "手动"),
                )
                self.jobsTable.setItem(row, 4, QTableWidgetItem(interval_text))

                last_run = job.get("last_run_at") or ""
                last_text = (
                    str(last_run).replace("T", " ").split(".", maxsplit=1)[0]
                    if last_run else "-"
                )
                self.jobsTable.setItem(row, 5, QTableWidgetItem(last_text))

                self.__set_job_actions(row, job, is_running)
        finally:
            self.jobsTable.setUpdatesEnabled(True)

    def __set_job_actions(self, row, job, is_running):
        """构建任务行操作按钮（紧凑图标按钮，带悬停提示）。"""
        old = self.jobsTable.cellWidget(row, 6)
        if old is not None:
            self.jobsTable.removeCellWidget(row, 6)
            old.deleteLater()

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(2)

        def _tool_button(icon_enum, tooltip, slot):
            btn = ToolButton(icon_enum, self.jobsTable)
            btn.setFixedSize(30, 30)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda _=False, j=job: slot(j))
            action_layout.addWidget(btn)
            return btn

        if is_running:
            _tool_button(
                FIF.CLOSE, tr("bisync.btn_cancel", "取消"), self.__cancel_job
            )
        else:
            _tool_button(
                FIF.SYNC, tr("bisync.btn_sync", "同步"), self.__run_job
            )

        _tool_button(FIF.EDIT, tr("bisync.btn_edit", "编辑"), self.__edit_job)

        if bool(job.get("enabled")):
            _tool_button(
                FIF.CANCEL, tr("bisync.btn_disable", "停用"), self.__toggle_job
            )
        else:
            _tool_button(
                FIF.ACCEPT, tr("bisync.btn_enable", "启用"), self.__toggle_job
            )

        _tool_button(FIF.DELETE, tr("bisync.btn_delete", "删除"), self.__delete_job)

        del_btn = PushButton(
            FIF.DELETE.icon(), tr("bisync.btn_delete", "删除"), self.jobsTable
        )
        del_btn.setFixedSize(64, 24)
        del_btn.clicked.connect(lambda _, j=job: self.__delete_job(j))
        action_layout.addWidget(del_btn)

        widget = QWidget()
        widget.setLayout(action_layout)
        self.jobsTable.setCellWidget(row, 6, widget)

    def __refresh_history(self):
        rows = self._manager.get_history(limit=200)
        self.historyTable.setRowCount(len(rows))
        self.historyTable.setUpdatesEnabled(False)
        try:
            for i, row in enumerate(rows):
                status_map = {
                    "completed": tr("bisync.h_completed", "成功"),
                    "failed": tr("bisync.h_failed", "失败"),
                    "cancelled": tr("bisync.h_cancelled", "已取消"),
                }
                self.historyTable.setItem(
                    i, 0, QTableWidgetItem(row.get("job_name", ""))
                )
                self.historyTable.setItem(
                    i, 1,
                    QTableWidgetItem(
                        status_map.get(row.get("status", ""), row.get("status", ""))
                    ),
                )
                self.historyTable.setItem(
                    i, 2, QTableWidgetItem(str(row.get("added", 0)))
                )
                self.historyTable.setItem(
                    i, 3, QTableWidgetItem(str(row.get("updated", 0)))
                )
                self.historyTable.setItem(
                    i, 4, QTableWidgetItem(str(row.get("downloaded", 0)))
                )
                self.historyTable.setItem(
                    i, 5, QTableWidgetItem(str(row.get("deleted", 0)))
                )
                self.historyTable.setItem(
                    i, 6, QTableWidgetItem(str(row.get("conflicts", 0)))
                )
                finished = row.get("finished_at") or ""
                time_text = str(finished).replace("T", " ").split(".", maxsplit=1)[0]
                self.historyTable.setItem(i, 7, QTableWidgetItem(time_text))
        finally:
            self.historyTable.setUpdatesEnabled(True)
