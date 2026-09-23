"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from dataclasses import dataclass
from enum import Enum


class BiSyncAction(str, Enum):
    """双向同步计划项的动作类型。"""

    CREATE_REMOTE_DIR = "create_remote_dir"
    UPLOAD = "upload"
    CREATE_LOCAL_DIR = "create_local_dir"
    DOWNLOAD = "download"
    CONFLICT_COPY = "conflict_copy"
    DELETE_REMOTE = "delete_remote"
    TRASH_LOCAL = "trash_local"
    REMOVE_LOCAL_DIR = "remove_local_dir"
    KEEP_LOCAL = "keep_local"
    KEEP_REMOTE = "keep_remote"
    TYPE_CONFLICT = "type_conflict"


class BiSyncItemStatus(str, Enum):
    """单个同步计划项的运行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class BiSyncEventType(str, Enum):
    """服务层与任务层之间的运行事件类型。"""

    RUN_STARTED = "run_started"
    PHASE_CHANGED = "phase_changed"
    PLAN_READY = "plan_ready"
    ITEM_STARTED = "item_started"
    ITEM_PROGRESS = "item_progress"
    ITEM_FINISHED = "item_finished"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class BiSyncPlanItem:
    """本次同步中一个可展示、可跟踪的操作。"""

    item_id: str
    action: BiSyncAction
    relative_path: str
    name: str
    is_dir: bool
    size: int = 0
    modified_at: int = 0


@dataclass(frozen=True, slots=True)
class BiSyncServiceEvent:
    """BiSyncService 发出的、与 Qt 无关的不可变运行事件。"""

    event_type: BiSyncEventType
    phase: str = ""
    items: tuple = ()
    item_id: str = ""
    transferred: int = 0
    total: int = 0
    status: BiSyncItemStatus | None = None
    message: str = ""
    success: bool | None = None
    summary: str = ""
    stats: dict | None = None


@dataclass(frozen=True, slots=True)
class BiSyncRunEvent:
    """任务层补充运行与任务身份后的事件。"""

    run_id: str
    job_id: int
    job_name: str
    event: BiSyncServiceEvent
