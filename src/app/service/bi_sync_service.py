"""
Copyright (C) 2026 123panNextGen
[https://github.com/123panNextGen/123pan]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import os
import time
from datetime import datetime
from pathlib import Path

from ..common.bi_sync_store import (
    CONFLICT_KEEP_BOTH,
    CONFLICT_LOCAL_WINS,
    CONFLICT_POLICIES,
    CONFLICT_REMOTE_WINS,
    BiSyncStore,
)
from ..common.log import get_logger
from .download_service import DownloadService
from .sync_service import (
    PHASE_DELETE,
    PHASE_SCAN_LOCAL,
    PHASE_SCAN_REMOTE,
    PHASE_UPLOAD,
    SyncService,
)

logger = get_logger(__name__)

# 双向同步新增阶段：从云端下载到本地
PHASE_PULL = "download"


class _CancelEventAdapter:
    """把同步 cancel 对象（is_cancelled 属性）适配成 threading.Event 的 is_set() 接口。"""

    def __init__(self, cancel):
        self._cancel = cancel

    def is_set(self):
        return getattr(self._cancel, "is_cancelled", False)


class BiSyncService(SyncService):
    """双向同步服务：本地目录 ↔ 123 云盘目录。

    继承 SyncService 复用本地/云端索引构建（build_local_index /
    build_remote_index），实现双向变更计算与执行：

    - 本地变化 → 上传覆盖云端
    - 云端变化 → 下载覆盖本地（下载后本地 mtime 对齐云端 UpdateAt）
    - 双方同时变化 → 冲突，按策略处理（默认保留双方副本）
    - 可选：本地删除 → 删云端（delete_remote）；云端删除 → 删本地（delete_local）
    - 状态快照（bi_sync_state）按 (job_id, rel_path) 记录两端版本，
      与单向同步的 sync_fingerprints 完全独立，互不影响。

    不持有 Qt 依赖，可由后台线程调用。
    """

    def __init__(self, session, account_name=None):
        super().__init__(session, account_name)
        self._download = DownloadService(session)
        self._store = BiSyncStore()

    # ---- 工具 ----

    @staticmethod
    def _remote_updateat(item):
        """读取云端文件更新时间戳（秒），缺失时返回 0。"""
        try:
            return int(item.get("UpdateAt", item.get("updateAt", 0)) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _remote_size(item):
        try:
            return int(item.get("Size", item.get("size", 0)) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _local_changed(local, st):
        """本地文件相对快照是否变化。

        快照缺失 → 视为变化；本地时间戳未知（None）→ 退化为按大小判断。
        """
        if st is None:
            return True
        if st["local_mtime"] is None:
            return local["size"] != st["local_size"]
        return (local["size"], local["mtime"]) != (
            st["local_size"], st["local_mtime"]
        )

    @classmethod
    def _remote_changed(cls, remote, st):
        """云端文件相对快照是否变化。

        快照缺失 → 视为变化；云端时间戳未知（None，上传后待对齐）→
        退化为按大小判断，下次扫描用远端索引补齐时间戳。
        """
        if st is None:
            return True
        if st["remote_updateat"] is None:
            return cls._remote_size(remote) != st["remote_size"]
        return (cls._remote_size(remote), cls._remote_updateat(remote)) != (
            st["remote_size"], st["remote_updateat"]
        )

    # ---- 变更计算 ----

    def compute_bi_changes(self, job, local_index, remote_index):
        """对比本地/云端索引与状态快照，生成双向同步计划。

        Args:
            job: bi_sync_jobs 行（dict）
            local_index: build_local_index 的结果
            remote_index: build_remote_index 的结果

        Returns:
            plan dict:
            - push_uploads: [(rel_path, abs_path, parent_rel, is_new)] 上传
            - push_dirs: [(rel_path, parent_rel)] 云端建目录
            - pull_downloads: [(rel_path, remote_item)] 下载
            - pull_dirs: [rel_path] 本地建目录
            - conflicts: [(rel_path, remote_item)] 冲突（保留双方副本：主路径上传本地版，云端版另存副本）
            - delete_remote: [rel_path] 删除云端条目
            - delete_local: [rel_path] 删除本地条目
            - type_conflicts: [rel_path] 文件/目录同名冲突（跳过并计失败）
        """
        job_id = int(job["id"])
        delete_remote = bool(job.get("delete_remote"))
        delete_local = bool(job.get("delete_local"))
        policy = job.get("conflict_policy") or CONFLICT_KEEP_BOTH
        if policy not in CONFLICT_POLICIES:
            policy = CONFLICT_KEEP_BOTH
        state = self._store.get_state(job_id)

        plan = {
            "push_uploads": [],
            "push_dirs": [],
            "pull_downloads": [],
            "pull_dirs": [],
            "conflicts": [],
            "delete_remote": [],
            "delete_local": [],
            "type_conflicts": [],
        }

        for rel in set(local_index) | set(remote_index) | set(state):
            local = local_index.get(rel)
            remote = remote_index.get(rel)
            st = state.get(rel)
            parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""

            local_is_file = local is not None and not local["is_dir"]
            local_is_dir = local is not None and local["is_dir"]
            remote_is_dir = remote is not None and int(remote.get("Type", 0)) == 1
            remote_is_file = remote is not None and not remote_is_dir

            # 两端都消失：清理残留快照
            if local is None and remote is None:
                self._store.remove_state(job_id, rel)
                continue

            # 文件/目录同名冲突：跳过并计失败，避免破坏任何一侧数据
            if (local_is_file and remote_is_dir) or (
                local_is_dir and remote_is_file
            ):
                plan["type_conflicts"].append(rel)
                continue

            # 目录：只做结构性补齐（创建缺失的一侧），不参与版本跟踪
            if local_is_dir:
                if remote is None:
                    plan["push_dirs"].append((rel, parent_rel))
                continue
            if remote_is_dir:
                if local is None:
                    plan["pull_dirs"].append(rel)
                continue

            # 首次接触且两端内容一致：直接对齐快照，不产生任何传输
            if (
                st is None
                and local_is_file
                and remote_is_file
                and self._remote_size(remote) == local["size"]
            ):
                self._store.set_state(
                    job_id, rel, local["size"], local["mtime"],
                    self._remote_size(remote), self._remote_updateat(remote),
                )
                continue

            if local_is_file and remote_is_file:
                lc = self._local_changed(local, st)
                rc = self._remote_changed(remote, st)
                if lc and rc:
                    # 双方同时变化 → 冲突
                    if policy == CONFLICT_KEEP_BOTH:
                        plan["conflicts"].append((rel, remote))
                        plan["push_uploads"].append(
                            (rel, local["abs"], parent_rel, False)
                        )
                    elif policy == CONFLICT_LOCAL_WINS:
                        plan["push_uploads"].append(
                            (rel, local["abs"], parent_rel, False)
                        )
                    else:  # CONFLICT_REMOTE_WINS
                        plan["pull_downloads"].append((rel, remote))
                elif lc:
                    plan["push_uploads"].append(
                        (rel, local["abs"], parent_rel, False)
                    )
                elif rc:
                    plan["pull_downloads"].append((rel, remote))
                else:
                    # 已同步；补齐未知时间戳（上传后云端时间戳待对齐）
                    if st["local_mtime"] is None or st["remote_updateat"] is None:
                        self._store.set_state(
                            job_id, rel, local["size"], local["mtime"],
                            self._remote_size(remote),
                            self._remote_updateat(remote),
                        )
                continue

            if local_is_file:
                # 云端缺失
                if st is None or self._local_changed(local, st):
                    plan["push_uploads"].append(
                        (rel, local["abs"], parent_rel, True)
                    )
                elif delete_local and st["remote_updateat"] is not None:
                    # 本地未变而云端被删除 → 删除本地
                    # （仅当云端副本此前被确认存在过，防止误删）
                    plan["delete_local"].append(rel)
                elif st["remote_updateat"] is None:
                    # 云端从未被确认存在（上次上传未验证成功等）→
                    # 重新上传，绝不删除本地文件
                    plan["push_uploads"].append(
                        (rel, local["abs"], parent_rel, True)
                    )
                # else: delete_local 关闭且云端副本确认过 → 保持不动（尊重云端删除）
                continue

            # remote_is_file（本地缺失）
            if st is None or self._remote_changed(remote, st):
                plan["pull_downloads"].append((rel, remote))
            elif delete_remote:
                # 云端未变而本地被删除 → 删除云端
                plan["delete_remote"].append(rel)

        return plan

    # ---- 执行 ----

    def run_bi_sync(self, job, progress_callback=None, cancel=None,
                    refresh_session=None):
        """执行一次完整双向同步。

        Args:
            job: bi_sync_jobs 行（dict）
            progress_callback: 可选 (rel_path, current, total, phase)，
                阶段为扫描时 rel_path 为 None
            cancel: 可选对象，具备 is_cancelled 属性
            refresh_session: 可选回调，token 过期时重新登录并返回 200 表示成功
                （由调用方传入，通常为 Pan123.login）

        Returns:
            (success, stats)，stats = {"added","updated","downloaded",
            "conflicts","deleted_remote","deleted_local","failed","skipped"}
        """
        job_id = int(job["id"])
        local_root = job["local_path"]
        remote_root = int(job["remote_dir_id"])
        stats = {
            "added": 0, "updated": 0, "downloaded": 0, "conflicts": 0,
            "deleted_remote": 0, "deleted_local": 0, "failed": 0,
            "skipped": 0,
        }

        def _cancelled():
            return cancel is not None and getattr(cancel, "is_cancelled", False)

        if _cancelled():
            return False, stats

        # 安全校验：本地目录必须存在，避免误删本地/云端数据
        if not os.path.isdir(local_root):
            logger.error("双向同步本地目录不存在或不可访问: %s", local_root)
            return False, stats

        # 1. 本地索引
        if progress_callback:
            progress_callback(None, 0, 0, PHASE_SCAN_LOCAL)
        local_index = self.build_local_index(local_root)

        # 2. 云端索引（失败必须中止，防止误传/误删）
        if progress_callback:
            progress_callback(None, 0, 0, PHASE_SCAN_REMOTE)
        remote_index = self.build_remote_index(remote_root)
        if remote_index is None:
            logger.error("获取云端目录失败，中止双向同步: dir_id=%s", remote_root)
            return False, stats

        # 3. 变更计划
        plan = self.compute_bi_changes(job, local_index, remote_index)
        logger.info(
            "双向同步计划: job=%s, 上传=%d, 建目录=%d, 下载=%d, 冲突=%d, "
            "删云端=%d, 删本地=%d",
            job.get("name"), len(plan["push_uploads"]), len(plan["push_dirs"]),
            len(plan["pull_downloads"]), len(plan["conflicts"]),
            len(plan["delete_remote"]), len(plan["delete_local"]),
        )

        # 4. 创建缺失的云端目录（顶层优先）
        dir_id_map = {
            rel: item["FileId"]
            for rel, item in remote_index.items()
            if int(item.get("Type", 0)) == 1
        }
        dir_id_map[""] = remote_root
        for rel, parent_rel in sorted(
            plan["push_dirs"], key=lambda x: x[0].count("/")
        ):
            if _cancelled():
                return False, stats
            parent_id = dir_id_map.get(parent_rel)
            if parent_id is None:
                logger.error("双向同步建目录失败，父目录缺失: %s", rel)
                stats["failed"] += 1
                continue
            name = rel.rsplit("/", 1)[-1]
            fid, err = self._file.create_folder(name, parent_id)
            if fid is None:
                logger.error("双向同步建目录失败: %s (%s)", rel, err)
                stats["failed"] += 1
                continue
            dir_id_map[rel] = fid
            remote_index.setdefault(rel, {"FileId": fid, "Type": 1})
            logger.debug("双向同步已创建云端目录: %s", rel)

        # 5. 上传本地变化
        total = len(plan["push_uploads"])
        for i, (rel, abs_path, parent_rel, is_new) in enumerate(
            plan["push_uploads"], start=1
        ):
            if _cancelled():
                return False, stats
            parent_id = dir_id_map.get(parent_rel)
            if parent_id is None:
                logger.error("双向同步上传失败，父目录缺失: %s", rel)
                stats["failed"] += 1
                continue
            if progress_callback:
                progress_callback(rel, i, total, PHASE_UPLOAD)
            try:
                # 统一覆盖语义（dup_choice=2）：重复上传时服务端按 MD5 复用，
                # 避免“新文件但服务端已有同名文件”时 5060 竞态；
                # 传入 refresh_session：token 过期时自动重登重试（与 UI 上传一致）
                result = self._upload.up_load(
                    abs_path, parent_id, dup_choice=2, task=cancel,
                    refresh_session=refresh_session,
                )
                if result == "已取消":
                    return False, stats
                # 验证服务端确实注册了文件（服务端偶发静默丢弃时保护本地数据，
                # 避免下次同步被误判为“云端已删除”而删本地）
                name = os.path.basename(abs_path)
                remote_item = self._verify_uploaded(
                    parent_id, name, refresh_session
                )
                if remote_item is None:
                    logger.error("双向同步上传未在云端确认: %s", rel)
                    stats["failed"] += 1
                    continue
                if is_new:
                    stats["added"] += 1
                else:
                    stats["updated"] += 1
                info = local_index[rel]
                # 以云端列表确认的实际值写快照（时间戳已确认，无需后续对齐）
                self._store.set_state(
                    job_id, rel, info["size"], info["mtime"],
                    self._remote_size(remote_item),
                    self._remote_updateat(remote_item),
                )
            except Exception as e:
                logger.error("双向同步上传失败: %s (%s)", rel, e)
                stats["failed"] += 1

        # 6. 创建缺失的本地目录
        for rel in sorted(plan["pull_dirs"], key=lambda r: r.count("/")):
            if _cancelled():
                return False, stats
            target = Path(local_root) / rel.replace("/", os.sep)
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.error("双向同步建本地目录失败: %s (%s)", rel, e)
                stats["failed"] += 1

        # 7. 下载云端变化
        total = len(plan["pull_downloads"])
        for i, (rel, item) in enumerate(plan["pull_downloads"], start=1):
            if _cancelled():
                return False, stats
            if progress_callback:
                progress_callback(rel, i, total, PHASE_PULL)
            try:
                if not self._download_one(item, local_root, rel, cancel):
                    return False, stats  # 已取消
                size = self._remote_size(item)
                updateat = self._remote_updateat(item)
                self._store.set_state(
                    job_id, rel, size, updateat, size, updateat
                )
                stats["downloaded"] += 1
            except Exception as e:
                logger.error("双向同步下载失败: %s (%s)", rel, e)
                stats["failed"] += 1

        # 8. 冲突保留双方副本：云端版本另存本地副本（主路径保留本地版本，
        #    副本作为新文件在下次同步时上传，两侧最终都保有双方内容）
        for rel, item in plan["conflicts"]:
            if _cancelled():
                return False, stats
            copy_rel, _ = self._conflict_copy_path(local_root, rel, item)
            if progress_callback:
                progress_callback(copy_rel, 0, 0, PHASE_PULL)
            try:
                if not self._download_one(item, local_root, copy_rel, cancel):
                    return False, stats  # 已取消
                # 副本不写快照：作为全新本地文件，下次同步自动上传到云端
                stats["downloaded"] += 1
                stats["conflicts"] += 1
                logger.info("双向同步冲突保留双方副本: %s → %s", rel, copy_rel)
            except Exception as e:
                logger.error("双向同步冲突副本下载失败: %s (%s)", rel, e)
                stats["failed"] += 1

        # 9. 删除云端多余条目（delete_remote），文件优先、目录自底向上
        if plan["delete_remote"]:
            file_dels = [
                r for r in plan["delete_remote"]
                if int(remote_index[r].get("Type", 0)) == 0
            ]
            dir_dels = sorted(
                (
                    r for r in plan["delete_remote"]
                    if int(remote_index[r].get("Type", 0)) == 1
                ),
                key=lambda x: -x.count("/"),
            )
            for rel in file_dels + dir_dels:
                if _cancelled():
                    return False, stats
                if progress_callback:
                    progress_callback(rel, 0, 0, PHASE_DELETE)
                try:
                    result = self._session.trash_file(
                        remote_index[rel], operation=True
                    )
                    if result.code == 0:
                        stats["deleted_remote"] += 1
                        self._store.remove_state(job_id, rel)
                    else:
                        logger.warning(
                            "双向同步删除云端失败: %s (code=%s)",
                            rel, result.code,
                        )
                        stats["failed"] += 1
                except Exception as e:
                    logger.error("双向同步删除云端异常: %s (%s)", rel, e)
                    stats["failed"] += 1

        # 10. 删除本地多余条目（delete_local），文件优先、目录自底向上仅删空目录
        if plan["delete_local"]:
            file_dels = [
                r for r in plan["delete_local"]
                if not local_index[r]["is_dir"]
            ]
            dir_dels = sorted(
                (r for r in plan["delete_local"] if local_index[r]["is_dir"]),
                key=lambda x: -x.count("/"),
            )
            for rel in file_dels:
                if _cancelled():
                    return False, stats
                if progress_callback:
                    progress_callback(rel, 0, 0, PHASE_DELETE)
                path = Path(local_root) / rel.replace("/", os.sep)
                try:
                    if path.exists() and path.is_file():
                        os.remove(path)
                    stats["deleted_local"] += 1
                    self._store.remove_state(job_id, rel)
                except OSError as e:
                    logger.error("双向同步删除本地文件失败: %s (%s)", rel, e)
                    stats["failed"] += 1
            for rel in dir_dels:
                if _cancelled():
                    return False, stats
                if progress_callback:
                    progress_callback(rel, 0, 0, PHASE_DELETE)
                path = Path(local_root) / rel.replace("/", os.sep)
                try:
                    if path.is_dir():
                        os.rmdir(path)  # 仅删空目录，防止误删未知内容
                    stats["deleted_local"] += 1
                    self._store.remove_state(job_id, rel)
                except OSError as e:
                    logger.warning(
                        "双向同步删除本地目录失败(仅删空目录): %s (%s)", rel, e
                    )
                    stats["failed"] += 1

        # 11. 文件/目录同名冲突：计失败（已在计划阶段跳过，不破坏两侧数据）
        for rel in plan["type_conflicts"]:
            stats["failed"] += 1
            logger.error("双向同步文件/目录同名冲突，已跳过: %s", rel)

        # 同步可能改变了云端结构，标记缓存失效
        self._file.mark_all_dirs_dirty()

        logger.info(
            "双向同步完成: job=%s, 新增=%d, 更新=%d, 下载=%d, 冲突=%d, "
            "删云端=%d, 删本地=%d, 失败=%d",
            job.get("name"), stats["added"], stats["updated"],
            stats["downloaded"], stats["conflicts"],
            stats["deleted_remote"], stats["deleted_local"], stats["failed"],
        )
        return True, stats

    # ---- 内部实现 ----

    def _verify_uploaded(self, parent_id, file_name, refresh_session=None):
        """确认文件已出现在云端目录中。

        服务端偶发“接口成功但文件未注册”时，等待 3 秒重查一次；
        token 过期时自动重新登录后重查；仍不存在则返回 None
        （调用方计失败且不写快照，下次同步自动重试）。
        """
        for attempt in range(2):
            code, items, *_ = self._file.get_dir_by_id(
                parent_id, all=True, limit=100, force_refresh=True
            )
            if code == 0 and items:
                for item in items:
                    if item.get("FileName") == file_name:
                        return item
            if code == 2 and refresh_session is not None:
                # token 过期：重新登录后立即重查（不计入等待重试）
                logger.info("双向同步上传验证遇到 token 过期，重新登录")
                try:
                    refresh_session()
                except Exception as e:
                    logger.error("双向同步重登失败: %s", e)
                continue
            if attempt == 0:
                time.sleep(3)
        return None

    def _download_one(self, item, local_root, rel, cancel):
        """下载单个云端文件到本地相对路径。

        Returns:
            True 成功；False 用户取消（调用方需中止整个同步）。
        失败（网络/链接错误等）抛出异常，由调用方计入失败并继续下一个文件。
        """
        size = self._remote_size(item)
        target = Path(local_root) / rel.replace("/", os.sep)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"创建本地目录失败: {e}") from e

        url = self._download.link_by_fileDetail(item)
        if not isinstance(url, str):
            raise RuntimeError(f"获取下载链接失败 (code={url})")

        cancel_event = _CancelEventAdapter(cancel) if cancel is not None else None
        ok = self._download.download_file(
            url, target, size, cancel_event=cancel_event
        )
        if not ok:
            if cancel is not None and getattr(cancel, "is_cancelled", False):
                return False
            raise RuntimeError("下载失败")

        # 本地 mtime 对齐云端 UpdateAt：跨机同步后指纹稳定
        updateat = self._remote_updateat(item)
        if updateat > 0:
            try:
                os.utime(target, (updateat, updateat))
            except OSError:
                pass
        return True

    def _conflict_copy_path(self, local_root, rel, item):
        """生成冲突副本的 (rel_path, abs_path)。

        命名：name（冲突副本 YYYYMMDD-HHMMSS）.ext，重名时追加序号。
        """
        updateat = self._remote_updateat(item)
        ts = (
            datetime.fromtimestamp(updateat).strftime("%Y%m%d-%H%M%S")
            if updateat > 0 else "unknown"
        )
        abs_path = Path(local_root) / rel.replace("/", os.sep)
        stem, suffix = abs_path.stem, abs_path.suffix
        candidate = abs_path
        candidate_rel = rel
        for n in range(100):
            extra = f"（冲突副本 {ts}）" if n == 0 else f"（冲突副本 {ts}-{n}）"
            candidate = abs_path.with_name(f"{stem}{extra}{suffix}")
            candidate_rel = os.path.relpath(candidate, local_root).replace(
                os.sep, "/"
            )
            if not candidate.exists():
                break
        return candidate_rel, candidate
