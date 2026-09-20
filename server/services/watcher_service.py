"""Folder watcher service for monitoring and auto-scraping."""

import asyncio
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import aiosqlite

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent

from server.core.db.connection import db_connection
from server.core.database import DATABASE_PATH
from server.models.organize import OrganizeMode
from server.models.storage import is_p115_virtual_path
from server.models.watcher import (
    DetectedFile,
    WatchedFolder,
    WatchedFolderCreate,
    WatchedFolderUpdate,
    WatcherMode,
    WatcherNotification,
    WatcherProvider,
    WatcherStatus,
    WatcherStatusResponse,
)
from server.services.file_io import check_file_cancelled, run_file_io

logger = logging.getLogger(__name__)

# Video file extensions to watch — 与 file_service.SUPPORTED_VIDEO_EXTENSIONS 保持一致
from server.services.file_service import SUPPORTED_VIDEO_EXTENSIONS as VIDEO_EXTENSIONS


def _report_background_failure(task: asyncio.Task, label: str) -> None:
    """Log unexpected task termination instead of leaving it unobserved."""
    if task.cancelled():
        return
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return
    if error is not None:
        logger.error(
            "%s stopped unexpectedly: %s",
            label,
            error,
            exc_info=(type(error), error, error.__traceback__),
        )


def _create_background_task(coro, label: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=label)
    task.add_done_callback(lambda completed: _report_background_failure(completed, label))
    return task


class VideoFileHandler(FileSystemEventHandler):
    """处理视频文件事件的处理器"""

    def __init__(self, folder: WatchedFolder, callback: Callable[[str, WatchedFolder], None]):
        self.folder = folder
        self.callback = callback

    def _is_video_file(self, path: str) -> bool:
        """检查是否为视频文件"""
        ext = Path(path).suffix.lower()
        return ext in VIDEO_EXTENSIONS

    def on_created(self, event: FileCreatedEvent) -> None:
        """文件创建事件"""
        if event.is_directory:
            return
        if self._is_video_file(event.src_path):
            logger.info(f"检测到新文件: {event.src_path}")
            self.callback(event.src_path, self.folder)

    def on_moved(self, event: FileMovedEvent) -> None:
        """文件移动事件（重命名）"""
        if event.is_directory:
            return
        if self._is_video_file(event.dest_path):
            logger.info(f"检测到移动文件: {event.dest_path}")
            self.callback(event.dest_path, self.folder)


class WatchStrategy(ABC):
    """监控策略抽象基类"""

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        self.folder = folder
        self.on_file_detected = on_file_detected
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass


class RealtimeStrategy(WatchStrategy):
    """实时监控策略 - 使用 watchdog"""

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._observer: Observer | None = None

    async def start(self) -> None:
        if self._running or not Path(self.folder.path).exists():
            return
        observer = Observer()
        handler = VideoFileHandler(self.folder, self.on_file_detected)
        observer.schedule(handler, self.folder.path, recursive=True)
        try:
            observer.start()
        except BaseException:
            observer.stop()
            raise
        self._observer = observer
        self._running = True
        logger.info(f"[实时模式] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        observer = self._observer
        self._observer = None
        if observer:
            observer.stop()
            await run_file_io(observer.join, 5)
            if observer.is_alive():
                self._observer = observer
                raise RuntimeError(
                    f"实时监控线程未在超时内停止: {self.folder.path}"
                )
        self._running = False


class CompatStrategy(WatchStrategy):
    """兼容模式策略 - 定时轮询扫描"""

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._scan_task: asyncio.Task | None = None
        self._known_files: set[str] = set()

    async def start(self) -> None:
        if self._running or not Path(self.folder.path).exists():
            return
        self._running = True
        try:
            await self._init_known_files()
        except BaseException:
            self._running = False
            raise
        self._scan_task = _create_background_task(
            self._scan_loop(), f"watcher-compat-{self.folder.id}"
        )
        logger.info(f"[兼容模式] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        self._running = False
        task = self._scan_task
        self._scan_task = None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._known_files.clear()

    def _local_video_paths(self) -> set[str]:
        paths = set()
        for root, _, files in os.walk(self.folder.path):
            check_file_cancelled()
            for f in files:
                check_file_cancelled()
                if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                    paths.add(str(Path(root) / f))
        return paths

    async def _init_known_files(self) -> None:
        self._known_files = await run_file_io(self._local_video_paths)

    async def _scan_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.folder.scan_interval_seconds)
                if not self._running:
                    break
                current = await run_file_io(self._local_video_paths)
                for fp in current - self._known_files:
                    self.on_file_detected(fp, self.folder)
                self._known_files = current
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "[兼容模式] 扫描出错 folder=%s id=%s",
                    self.folder.path,
                    self.folder.id,
                )


class P115ScanStrategy(WatchStrategy):
    """115 网盘监控策略 - 定时轮询 115 目录对比 file_id 找新增文件。

    115 文件已稳定（不像本地文件可能在写入中），检测到新文件后直接加入
    _pending_files（复用稳定等待逻辑统一处理）。
    """

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._scan_task: asyncio.Task | None = None
        self._known_file_ids: set[str] = set()
        self._baseline_ready = False
        self._p115_service: Any = None
        # 新检测文件的元数据（file_id/parent_id），供 _create_jobs_for_files 使用
        self.detected_meta: dict[str, dict] = {}

    async def _get_p115_service(self):
        """懒加载 P115Service（避免 import 循环 + 启动时未登录报错）。"""
        if self._p115_service is None:
            from server.services.p115_service import P115Service
            from server.services.config_service import ConfigService
            self._p115_service = P115Service(ConfigService())
        return self._p115_service

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # 初始化已知文件集合（首次扫描不触发新增）
        try:
            await self._init_known_files()
        except Exception as e:
            logger.warning(f"[115监控] 初始扫描失败（可能未登录）: {e}")
        self._scan_task = _create_background_task(
            self._scan_loop(), f"watcher-p115-scan-{self.folder.id}"
        )
        logger.info(f"[115监控] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        self._running = False
        task = self._scan_task
        self._scan_task = None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._known_file_ids.clear()
        self._baseline_ready = False

    async def _init_known_files(self) -> None:
        """首次扫描记录已有文件，不触发回调。"""
        svc = await self._get_p115_service()
        entries = await svc.scan_folder(
            path=self.folder.path,
            file_id=self.folder.file_id,
        )
        self._known_file_ids = self._collect_file_ids(entries)
        self._baseline_ready = True

    @staticmethod
    def _collect_file_ids(entries: list[dict[str, Any]]) -> set[str]:
        return {
            str(entry["file_id"])
            for entry in entries
            if entry.get("file_id") not in (None, "", "0")
        }

    def _apply_scan_snapshot(self, entries: list[dict[str, Any]]) -> None:
        """Record a recovered baseline or emit only files new to a valid baseline."""
        current_ids = self._collect_file_ids(entries)
        if not self._baseline_ready:
            self._known_file_ids = current_ids
            self._baseline_ready = True
            logger.info("[115监控] 基线恢复完成，未将已有文件作为新增任务")
            return

        for entry in entries:
            fid = entry.get("file_id")
            if fid in (None, "", "0"):
                continue
            fid_str = str(fid)
            if fid_str in self._known_file_ids:
                continue
            file_path = entry.get("path", "")
            if file_path:
                self.detected_meta[file_path] = {
                    "file_id": fid_str,
                    "parent_id": entry.get("parent_id"),
                    "size": entry.get("size", 0),
                }
                self.on_file_detected(file_path, self.folder)
            logger.info(f"[115监控] 检测到新文件: {file_path or fid_str}")
        self._known_file_ids = current_ids

    async def _scan_loop(self) -> None:
        """定时轮询 115 目录，对比 file_id 找新增视频文件。"""
        while self._running:
            try:
                await asyncio.sleep(self.folder.scan_interval_seconds)
                if not self._running:
                    break
                svc = await self._get_p115_service()
                entries = await svc.scan_folder(
                    path=self.folder.path,
                    file_id=self.folder.file_id,
                )
                self._apply_scan_snapshot(entries)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "[115监控] 扫描出错 folder=%s id=%s",
                    self.folder.path,
                    self.folder.id,
                )


class P115EventStrategy(WatchStrategy):
    """115 网盘事件监控策略 - 定时拉取生活事件 API 找新增视频文件。

    相比 P115ScanStrategy 的全量目录扫描，事件模式只处理增量事件，
    响应更快、开销更低。依赖 115 生活事件 API（life_list）。
    """

    # 表示文件新增类的行为类型（与 115 life_list 实际返回值对齐）
    NEW_FILE_BEHAVIORS = {
        "upload_file", "copy_file", "move_file",
        "receive", "recv", "receive_file",
        "offline_download",
    }
    PROCESSED_FILE_LIMIT = 10_000

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._scan_task: asyncio.Task | None = None
        self._last_update_time: int = 0  # 已处理的最大 update_time（去重）
        self._client: Any = None
        self.detected_meta: dict[str, dict] = {}
        # 监控目录及其所有子目录的 id 集合（用于 parent_id 匹配）
        self._watched_dir_ids: set[str] = set()
        # 已处理过的 file_id 集合（防止刮削产生的整理事件导致循环）
        self._processed_file_ids: set[str] = set()
        self._processed_file_order: deque[str] = deque()
        self._initialized = False
        self._loop_count = 0

    async def _get_client(self):
        """懒加载 115 client。"""
        if self._client is None:
            from server.services.p115_service import P115Service
            from server.services.config_service import ConfigService
            svc = P115Service(ConfigService())
            config = await svc.config_service.get_115_config()
            if not config.is_logged_in:
                raise ValueError("115 未登录")
            self._client = await svc._load_p115_client_with_config(config)
        return self._client

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # 首次初始化：收集监控目录的子目录 id + 记录当前最大事件时间
        try:
            await self._init()
            self._initialized = True
        except Exception as e:
            logger.warning(f"[115事件] 初始化失败（可能未登录）: {e}")
        self._scan_task = _create_background_task(
            self._event_loop(), f"watcher-p115-event-{self.folder.id}"
        )
        logger.info(f"[115事件] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        self._running = False
        task = self._scan_task
        self._scan_task = None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._initialized = False
        self._processed_file_ids.clear()
        self._processed_file_order.clear()

    async def _init(self) -> None:
        """首次初始化：收集子目录 id + 记录当前最大事件时间（不触发回调）。"""
        # 收集监控目录及其所有子目录的 id（用于 parent_id 匹配）
        await self._collect_dir_ids()

        # 记录当前最大事件时间
        client = await self._get_client()
        resp = await client.life_list(0, async_=True)
        events = self._extract_events(resp)
        if events:
            self._last_update_time = max(e["update_time"] for e in events)
        else:
            # An empty baseline still represents "now". Keeping zero here
            # would allow retained historical events to be replayed later.
            self._last_update_time = int(time.time())

    async def _collect_dir_ids(self) -> None:
        """递归收集监控目录及其所有子目录的 id。"""
        from server.services.p115_service import P115Service
        from server.services.config_service import ConfigService

        svc = P115Service(ConfigService())
        self._watched_dir_ids.clear()
        if self.folder.file_id:
            self._watched_dir_ids.add(str(self.folder.file_id))
        await self._collect_subdir_ids(svc, self.folder.path, self.folder.file_id)
        if not self._watched_dir_ids:
            raise ValueError("无法确认 115 监控目录 ID，事件监控保持关闭")

    async def _collect_subdir_ids(
        self, svc: Any, path: str, file_id: str | None, depth: int = 0
    ) -> None:
        """递归收集子目录 id（限制深度避免过深）。"""
        if depth > 5:
            return
        try:
            result = await svc.browse(path=path, file_id=file_id, page=1, page_size=100)
            current_file_id = result.get("current_file_id")
            if current_file_id not in (None, ""):
                self._watched_dir_ids.add(str(current_file_id))
            for entry in result.get("entries", []):
                if entry.get("is_dir") and entry.get("file_id"):
                    self._watched_dir_ids.add(str(entry["file_id"]))
                    # 递归收集子目录
                    await self._collect_subdir_ids(
                        svc, entry["path"], entry["file_id"], depth + 1
                    )
        except Exception as exc:
            logger.warning(
                "[115事件] 收集子目录失败 path=%s file_id=%s depth=%s: %s",
                path,
                file_id,
                depth,
                exc,
                exc_info=True,
            )

    def _extract_events(self, resp: Any) -> list[dict]:
        """从 life_list 响应提取事件列表。"""
        if not isinstance(resp, dict) or not resp.get("state"):
            return []
        data = resp.get("data") or {}
        return data.get("list") or []

    async def _event_loop(self) -> None:
        """定时拉取生活事件，找新增视频文件。"""
        while self._running:
            try:
                await asyncio.sleep(self.folder.scan_interval_seconds)
                if not self._running:
                    break
                if not self._initialized:
                    await self._init()
                    self._initialized = True
                    logger.info("[115事件] 初始化恢复完成，从当前事件位置开始监控")
                    continue
                client = await self._get_client()
                resp = await client.life_list(0, async_=True)
                events = self._extract_events(resp)
                if not events:
                    continue

                new_max_time = self._last_update_time
                for event in events:
                    evt_time = event.get("update_time", 0)
                    if evt_time <= self._last_update_time:
                        continue  # 已处理
                    new_max_time = max(new_max_time, evt_time)

                    behavior = event.get("behavior_type", "")
                    if behavior not in self.NEW_FILE_BEHAVIORS:
                        continue

                    for item in event.get("items", []):
                        self._process_event_item(item)

                self._last_update_time = new_max_time

                # 定期刷新子目录 id 集合（每 10 轮刷新一次）
                if self._loop_count % 10 == 0:
                    try:
                        await self._collect_dir_ids()
                    except Exception as exc:
                        logger.warning(
                            "[115事件] 刷新子目录失败 folder=%s id=%s: %s",
                            self.folder.path,
                            self.folder.id,
                            exc,
                            exc_info=True,
                        )
                self._loop_count += 1

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "[115事件] 拉取出错 folder=%s id=%s last_update=%s",
                    self.folder.path,
                    self.folder.id,
                    self._last_update_time,
                )

    def _remember_processed_file_id(self, file_id: str) -> None:
        if file_id in self._processed_file_ids:
            return
        if len(self._processed_file_order) >= self.PROCESSED_FILE_LIMIT:
            expired = self._processed_file_order.popleft()
            self._processed_file_ids.discard(expired)
        self._processed_file_order.append(file_id)
        self._processed_file_ids.add(file_id)

    def _process_event_item(self, item: dict) -> None:
        """处理单个事件项，判断是否为监控目录内的视频新增。"""
        file_id = item.get("file_id")
        file_name = item.get("file_name", "")
        ico = item.get("ico", "")
        parent_id = str(item.get("parent_id", ""))

        if file_id in (None, "", "0"):
            logger.warning("[115事件] 忽略缺少有效文件 ID 的事件项: %s", file_name)
            return

        # file_id 去重：已处理过的文件不再处理（防止刮削整理事件导致循环）
        if file_id and str(file_id) in self._processed_file_ids:
            return

        # 检查是否视频文件（ico 字段是扩展名，如 "mp4"）
        if f".{ico}" not in VIDEO_EXTENSIONS:
            return

        # 路径匹配：parent_id 在监控目录的子目录 id 集合里才处理
        if not self._watched_dir_ids or parent_id not in self._watched_dir_ids:
            return

        # 记录已处理
        self._remember_processed_file_id(str(file_id))

        # 构造虚拟路径（115 路径）
        # parent_name 是文件所在目录名。如果等于监控目录名（直接子级），
        # 不重复拼接；只有更深的子目录才拼。
        parent_name = item.get("parent_name", "")
        folder_base_name = self.folder.path.rstrip("/").rsplit("/", 1)[-1] if self.folder.path else ""
        if parent_name and parent_name != folder_base_name:
            virtual_path = f"{self.folder.path}/{parent_name}/{file_name}"
        else:
            virtual_path = f"{self.folder.path}/{file_name}"

        self.detected_meta[virtual_path] = {
            "file_id": str(file_id),
            "parent_id": parent_id,
            "size": item.get("file_size", 0),
        }
        self.on_file_detected(virtual_path, self.folder)
        logger.info(f"[115事件] 检测到新文件: {file_name}")


class WatcherService:
    """Service for folder watching and auto-scraping."""

    def __init__(self, db_path: Path | None = None):
        """Initialize watcher service."""
        self.db_path = db_path or DATABASE_PATH
        self._status = WatcherStatus.STOPPED
        self._running = False
        self._strategies: dict[str, WatchStrategy] = {}  # folder_id -> strategy
        self._pending_files: dict[str, tuple[str, float, WatchedFolder]] = {}  # path -> (path, detect_time, folder)
        self._last_detection: datetime | None = None
        self._on_files_detected: Callable[[WatcherNotification], None] | None = None
        self._process_task: asyncio.Task | None = None
        self._initial_scan_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def _ensure_db(self) -> None:
        """Ensure database directory exists and run migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with db_connection(self.db_path) as db:
            # 兼容旧数据库：添加缺失列
            cursor = await db.execute("PRAGMA table_info(watched_folders)")
            columns = [row[1] for row in await cursor.fetchall()]
            if "mode" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN mode TEXT DEFAULT 'realtime'")
            if "output_dir" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN output_dir TEXT")
            if "provider" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN provider TEXT DEFAULT 'local'")
            if "file_id" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN file_id TEXT")
            await db.commit()

    async def create_folder(self, folder: WatchedFolderCreate) -> WatchedFolder:
        """Create a new watched folder."""
        await self._ensure_db()

        folder_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        async with db_connection(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO watched_folders
                (id, path, enabled, mode, scan_interval_seconds, file_stable_seconds, auto_scrape, output_dir, provider, file_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    folder_id,
                    folder.path,
                    1 if folder.enabled else 0,
                    folder.mode.value,
                    folder.scan_interval_seconds,
                    folder.file_stable_seconds,
                    1 if folder.auto_scrape else 0,
                    folder.output_dir,
                    folder.provider,
                    folder.file_id,
                    now.isoformat(),
                ),
            )
            await db.commit()

        new_folder = WatchedFolder(
            id=folder_id,
            path=folder.path,
            enabled=folder.enabled,
            mode=folder.mode,
            scan_interval_seconds=folder.scan_interval_seconds,
            file_stable_seconds=folder.file_stable_seconds,
            auto_scrape=folder.auto_scrape,
            output_dir=folder.output_dir,
            provider=folder.provider,
            file_id=folder.file_id,
            last_scan=None,
            created_at=now,
        )

        # 如果服务正在运行且文件夹启用，立即启动监控
        if folder.enabled:
            async with self._lifecycle_lock:
                if self._running:
                    await self._start_folder_watch(new_folder)

        return new_folder

    async def list_folders(self) -> tuple[list[WatchedFolder], int]:
        """List all watched folders."""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute("SELECT COUNT(*) as count FROM watched_folders")
            row = await cursor.fetchone()
            total = row["count"] if row else 0

            cursor = await db.execute(
                "SELECT * FROM watched_folders ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()

        folders = [self._row_to_folder(row) for row in rows]
        return folders, total

    async def get_folder(self, folder_id: str) -> WatchedFolder | None:
        """Get a watched folder by ID."""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM watched_folders WHERE id = ?",
                (folder_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_folder(row)

    async def update_folder(
        self, folder_id: str, update: WatchedFolderUpdate
    ) -> WatchedFolder | None:
        """Update a watched folder."""
        await self._ensure_db()

        folder = await self.get_folder(folder_id)
        if folder is None:
            return None

        candidate_data = folder.model_dump()
        for field_name in update.model_fields_set:
            value = getattr(update, field_name)
            if value is not None:
                candidate_data[field_name] = value
        # Validate the merged state before persisting a partial update.
        WatchedFolder(**candidate_data)

        updates = []
        values = []

        if update.path is not None:
            updates.append("path = ?")
            values.append(update.path)
        if update.enabled is not None:
            updates.append("enabled = ?")
            values.append(1 if update.enabled else 0)
        if update.mode is not None:
            updates.append("mode = ?")
            values.append(update.mode.value)
        if update.scan_interval_seconds is not None:
            updates.append("scan_interval_seconds = ?")
            values.append(update.scan_interval_seconds)
        if update.file_stable_seconds is not None:
            updates.append("file_stable_seconds = ?")
            values.append(update.file_stable_seconds)
        if update.auto_scrape is not None:
            updates.append("auto_scrape = ?")
            values.append(1 if update.auto_scrape else 0)
        if update.output_dir is not None:
            updates.append("output_dir = ?")
            values.append(update.output_dir)
        if update.provider is not None:
            updates.append("provider = ?")
            values.append(update.provider)
        if update.file_id is not None:
            updates.append("file_id = ?")
            values.append(update.file_id)

        if updates:
            values.append(folder_id)
            async with db_connection(self.db_path) as db:
                await db.execute(
                    f"UPDATE watched_folders SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                await db.commit()

        updated_folder = await self.get_folder(folder_id)

        # 如果服务正在运行，重启该文件夹的监控
        if updated_folder:
            async with self._lifecycle_lock:
                if self._running:
                    await self._restart_folder_watch(updated_folder)

        return updated_folder

    async def delete_folder(self, folder_id: str) -> bool:
        """Delete a watched folder."""
        await self._ensure_db()

        # 先停止该文件夹的监控
        async with self._lifecycle_lock:
            if folder_id in self._strategies:
                await self._stop_folder_watch(folder_id)

        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM watched_folders WHERE id = ?",
                (folder_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_status(self) -> WatcherStatusResponse:
        """Get watcher status."""
        return WatcherStatusResponse(
            status=self._status,
            active_watchers=len(self._strategies),
            last_detection=self._last_detection,
            pending_files=len(self._pending_files),
        )

    async def _start_folder_watch(self, folder: WatchedFolder) -> None:
        """启动单个文件夹的监控"""
        if folder.id in self._strategies:
            return
        # 根据 provider + mode 选择策略
        if folder.provider == "115":
            if folder.mode == WatcherMode.EVENT:
                strategy: WatchStrategy = P115EventStrategy(folder, self._on_file_detected)
            else:
                strategy = P115ScanStrategy(folder, self._on_file_detected)
        elif folder.mode == WatcherMode.REALTIME:
            strategy = RealtimeStrategy(folder, self._on_file_detected)
        else:
            strategy = CompatStrategy(folder, self._on_file_detected)
        await strategy.start()
        if strategy.running:
            self._strategies[folder.id] = strategy
        else:
            logger.warning(
                "监控目录未启动 folder=%s id=%s provider=%s mode=%s",
                folder.path,
                folder.id,
                folder.provider,
                folder.mode.value,
            )

    async def _stop_folder_watch(self, folder_id: str) -> None:
        """停止单个文件夹的监控"""
        if folder_id in self._strategies:
            await self._strategies[folder_id].stop()
            del self._strategies[folder_id]

    async def _restart_folder_watch(self, folder: WatchedFolder) -> None:
        """重启单个文件夹的监控"""
        await self._stop_folder_watch(folder.id)
        if folder.enabled:
            await self._start_folder_watch(folder)

    async def start(
        self, on_files_detected: Callable[[WatcherNotification], None] | None = None
    ) -> None:
        """Start the watcher service."""
        async with self._lifecycle_lock:
            if self._running:
                return
            if self._strategies:
                await self._stop_locked()
                if self._strategies:
                    raise RuntimeError("仍有监控策略未能停止，拒绝重复启动")

            self._on_files_detected = on_files_detected
            try:
                # 获取所有启用的监控文件夹
                folders, _ = await self.list_folders()
                enabled_folders = [f for f in folders if f.enabled]

                if not enabled_folders:
                    logger.warning("没有启用的监控文件夹")

                # 为每个文件夹启动独立的监控策略
                for folder in enabled_folders:
                    await self._start_folder_watch(folder)

                self._running = True
                self._status = WatcherStatus.RUNNING
                self._process_task = _create_background_task(
                    self._process_pending_files(), "watcher-pending-files"
                )
                self._initial_scan_task = _create_background_task(
                    self._initial_scan(enabled_folders), "watcher-initial-scan"
                )
            except BaseException:
                await self._stop_locked()
                raise

            logger.info(f"监控服务已启动，共 {len(self._strategies)} 个文件夹")

    async def _initial_scan(self, folders: list[WatchedFolder]) -> None:
        """启动时执行一次全量扫描，跳过已有待处理任务的文件"""
        from server.services.scrape_job_service import ScrapeJobService
        scrape_service = ScrapeJobService()
        pending_paths = await scrape_service.get_pending_file_paths()
        logger.info(f"已有 {len(pending_paths)} 个待处理任务，初始扫描将跳过这些文件")

        for folder in folders:
            # 115 网盘目录：用 P115Service 扫描（不走本地 os.walk）
            if folder.provider == "115":
                await self._initial_scan_p115(folder, pending_paths)
                continue

            logger.info(f"扫描文件夹: {folder.path}")
            stable_files, unstable = await run_file_io(self._scan_initial_local, folder, pending_paths)
            self._pending_files.update(unstable)

            async with db_connection(self.db_path) as db:
                await db.execute(
                    "UPDATE watched_folders SET last_scan = ? WHERE id = ?",
                    (datetime.now().isoformat(), folder.id),
                )
                await db.commit()

            if stable_files and folder.auto_scrape:
                logger.info(f"初始扫描发现 {len(stable_files)} 个稳定文件")
                completed = await self._create_jobs_for_files(stable_files, folder)
                self._queue_failed_files(stable_files, folder, completed)

    @staticmethod
    def _scan_initial_local(folder: WatchedFolder, pending_paths: set[str]):
        stable_files = []
        unstable = {}
        current_time = time.time()
        for root, _, files in os.walk(folder.path):
            check_file_cancelled()
            for filename in files:
                check_file_cancelled()
                if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                path = Path(root) / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if current_time - stat.st_mtime >= folder.file_stable_seconds:
                    if str(path) not in pending_paths:
                        stable_files.append(DetectedFile(
                            path=str(path), detected_at=datetime.now(), file_size=stat.st_size, stable=True,
                        ))
                else:
                    unstable[str(path)] = (str(path), current_time, folder)
        return stable_files, unstable

    async def _initial_scan_p115(self, folder: WatchedFolder, pending_paths: set[str]) -> None:
        """启动时扫描 115 目录（不走本地 os.walk）。"""
        try:
            from server.services.p115_service import P115Service
            from server.services.config_service import ConfigService
            svc = P115Service(ConfigService())
            entries = await svc.scan_folder(path=folder.path, file_id=folder.file_id)
        except Exception as exc:
            logger.warning(
                "[115监控] 初始扫描失败 folder=%s id=%s: %s",
                folder.path,
                folder.id,
                exc,
                exc_info=True,
            )
            return

        stable_files: list[DetectedFile] = []
        for entry in entries:
            file_path = entry.get("path", "")
            if file_path in pending_paths:
                continue
            stable_files.append(
                DetectedFile(
                    path=file_path,
                    detected_at=datetime.now(),
                    file_size=entry.get("size") or 0,
                    stable=True,
                    file_id=entry.get("file_id"),
                    parent_id=entry.get("parent_id"),
                )
            )

        async with db_connection(self.db_path) as db:
            await db.execute(
                "UPDATE watched_folders SET last_scan = ? WHERE id = ?",
                (datetime.now().isoformat(), folder.id),
            )
            await db.commit()

        if stable_files and folder.auto_scrape:
            logger.info(f"[115监控] 初始扫描发现 {len(stable_files)} 个文件")
            completed = await self._create_jobs_for_files(stable_files, folder)
            self._queue_failed_files(stable_files, folder, completed)

    def _on_file_detected(self, path: str, folder: WatchedFolder) -> None:
        """文件检测回调"""
        self._last_detection = datetime.now()
        self._pending_files[path] = (path, time.time(), folder)
        logger.info(f"文件加入待处理队列: {path}")

    def _get_p115_meta(self, folder_id: str, file_path: str) -> dict | None:
        """从 P115 策略的 detected_meta 获取文件的 file_id/parent_id。"""
        strategy = self._strategies.get(folder_id)
        if strategy and isinstance(strategy, (P115ScanStrategy, P115EventStrategy)):
            return strategy.detected_meta.get(file_path)
        return None

    def _discard_p115_meta(self, folder_id: str, file_path: str) -> None:
        strategy = self._strategies.get(folder_id)
        if strategy and isinstance(strategy, (P115ScanStrategy, P115EventStrategy)):
            strategy.detected_meta.pop(file_path, None)

    def _queue_failed_files(
        self,
        files: list[DetectedFile],
        folder: WatchedFolder,
        completed: set[str],
    ) -> None:
        """Keep files whose jobs were not created so the watcher can retry them."""
        retry_at = time.time()
        strategy = self._strategies.get(folder.id)
        for file in files:
            if file.path in completed:
                continue
            if (
                folder.provider == "115"
                and isinstance(strategy, (P115ScanStrategy, P115EventStrategy))
            ):
                strategy.detected_meta[file.path] = {
                    "file_id": file.file_id,
                    "parent_id": file.parent_id,
                    "size": file.file_size,
                }
            self._pending_files[file.path] = (file.path, retry_at, folder)

    def _remove_pending_paths(self, paths: set[str], folder: WatchedFolder) -> None:
        for path in paths:
            self._pending_files.pop(path, None)
            if folder.provider == "115":
                self._discard_p115_meta(folder.id, path)

    async def _process_pending_once(self) -> None:
        """Process one stable-file batch without dropping failed task creations."""
        if not self._pending_files:
            return

        current_time = time.time()
        stable_by_folder: dict[str, tuple[list[DetectedFile], WatchedFolder]] = {}
        missing_local_paths: set[str] = set()

        for path, (file_path, detect_time, folder) in list(self._pending_files.items()):
            age = current_time - detect_time
            if age < folder.file_stable_seconds:
                continue
            if folder.provider == "115":
                meta = self._get_p115_meta(folder.id, file_path)
                key = folder.id
                if key not in stable_by_folder:
                    stable_by_folder[key] = ([], folder)
                stable_by_folder[key][0].append(
                    DetectedFile(
                        path=file_path,
                        detected_at=datetime.now(),
                        file_size=meta.get("size", 0) if meta else 0,
                        stable=True,
                        file_id=meta.get("file_id") if meta else None,
                        parent_id=meta.get("parent_id") if meta else None,
                    )
                )
                continue

            try:
                stat = Path(file_path).stat()
                if current_time - stat.st_mtime < folder.file_stable_seconds:
                    continue
                key = folder.id
                if key not in stable_by_folder:
                    stable_by_folder[key] = ([], folder)
                stable_by_folder[key][0].append(
                    DetectedFile(
                        path=file_path,
                        detected_at=datetime.now(),
                        file_size=stat.st_size,
                        stable=True,
                    )
                )
            except OSError:
                missing_local_paths.add(path)

        for path in missing_local_paths:
            self._pending_files.pop(path, None)

        for files, folder in stable_by_folder.values():
            paths = {file.path for file in files}
            if not folder.auto_scrape:
                self._remove_pending_paths(paths, folder)
                continue
            logger.info(f"处理 {len(files)} 个稳定文件 (folder={folder.path})")
            completed = await self._create_jobs_for_files(files, folder)
            self._remove_pending_paths(completed, folder)
            # Delay retries instead of spinning every watcher loop.
            for file in files:
                if file.path not in completed and file.path in self._pending_files:
                    self._pending_files[file.path] = (file.path, current_time, folder)

    async def _process_pending_files(self) -> None:
        """处理待处理文件的后台任务"""
        while self._running:
            try:
                await asyncio.sleep(5)
                await self._process_pending_once()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "处理待处理文件时出错 pending=%s strategies=%s",
                    len(self._pending_files),
                    len(self._strategies),
                )

    async def stop(self) -> None:
        """Stop the watcher service."""
        async with self._lifecycle_lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        """Stop all watcher resources while the lifecycle lock is held."""
        self._running = False
        self._status = WatcherStatus.STOPPED

        tasks = [
            task
            for task in (self._process_task, self._initial_scan_task)
            if task is not None
        ]
        self._process_task = None
        self._initial_scan_task = None
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        strategies = list(self._strategies.items())
        self._strategies.clear()
        if strategies:
            results = await asyncio.gather(
                *(strategy.stop() for _, strategy in strategies),
                return_exceptions=True,
            )
            for (folder_id, strategy), result in zip(strategies, results):
                if isinstance(result, BaseException):
                    self._strategies[folder_id] = strategy
                    self._status = WatcherStatus.ERROR
                    logger.error(
                        "停止监控策略失败 folder=%s path=%s: %s",
                        folder_id,
                        strategy.folder.path,
                        result,
                        exc_info=(type(result), result, result.__traceback__),
                    )

        if self._status == WatcherStatus.ERROR:
            logger.warning("监控服务停止时仍有未清理的策略")
        else:
            logger.info("监控服务已停止")

    async def _create_jobs_for_files(
        self, files: list[DetectedFile], folder: WatchedFolder | None = None
    ) -> set[str]:
        """为检测到的文件创建刮削任务。

        folder.output_dir 优先于全局整理目录配置。
        """
        from server.services.scrape_job_service import ScrapeJobService
        from server.services.config_service import ConfigService
        from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource

        try:
            config_service = ConfigService()
            organize_config = await config_service.get_organize_config()
        except Exception:
            logger.exception("读取整理配置失败，监控文件将保留等待重试")
            return set()

        # 优先用 folder 独立配置，回退全局配置
        organize_dir = folder.output_dir if folder and folder.output_dir else organize_config.organize_dir
        metadata_dir = organize_config.metadata_dir
        link_mode = organize_config.organize_mode  # 读取整理模式配置

        if not organize_dir:
            logger.warning("未配置整理目录，跳过创建任务")
            return set()

        scrape_service = ScrapeJobService()

        # 对 115 folder 预解析 output_locator（整理目标目录的 115 file_id）
        from server.models.storage import StorageLocator, StorageProvider
        output_locator = None
        if (
            folder
            and folder.provider == "115"
            and organize_dir
            and is_p115_virtual_path(organize_dir)
        ):
            try:
                from server.services.p115_service import P115Service
                p115_svc = P115Service(config_service)
                cfg = await config_service.get_115_config()
                if cfg.is_logged_in:
                    client = await p115_svc._load_p115_client_with_config(cfg)
                    normalized = p115_svc._normalize_virtual_path(organize_dir)
                    out_dir_id = await p115_svc._resolve_directory_id(
                        client=client, path=normalized, file_id=None,
                    )
                    output_locator = StorageLocator(
                        provider=StorageProvider.P115,
                        path=organize_dir,
                        file_id=str(out_dir_id),
                        is_dir=True,
                    )
            except Exception as exc:
                logger.warning(
                    "[115监控] 解析输出目录失败 folder=%s output=%s: %s",
                    folder.id,
                    organize_dir,
                    exc,
                    exc_info=True,
                )

        completed: set[str] = set()
        for file in files:
            try:
                logger.info(f"为文件创建刮削任务: {file.path}")

                # 115 源文件构造 file_locator（携带 file_id 以便刮削下载/在线处理）
                file_locator = None
                if folder and folder.provider == "115" and file.file_id:
                    file_locator = StorageLocator(
                        provider=StorageProvider.P115,
                        path=file.path,
                        file_id=file.file_id,
                        parent_id=file.parent_id or folder.file_id,
                        is_dir=False,
                    )

                # A configured 115 watcher is itself the user's authorization to
                # process new cloud files. Preserve that workflow while manual
                # jobs still require their explicit local-download switch.
                allow_local_output = bool(
                    file_locator
                    and file_locator.provider == StorageProvider.P115
                    and not is_p115_virtual_path(organize_dir)
                )
                effective_link_mode = link_mode
                if (
                    file_locator
                    and file_locator.provider == StorageProvider.P115
                    and (
                        allow_local_output
                        or link_mode not in (OrganizeMode.COPY, OrganizeMode.MOVE)
                    )
                    and link_mode != OrganizeMode.COPY
                ):
                    effective_link_mode = OrganizeMode.COPY
                    if allow_local_output:
                        logger.warning(
                            "115 监控源下载到本地仅支持复制，任务将改用复制模式: %s",
                            file.path,
                        )
                    else:
                        logger.warning(
                            "115 监控源不支持 %s，任务将改用复制模式: %s",
                            link_mode.value,
                            file.path,
                        )

                job_create = ScrapeJobCreate(
                    file_path=file.path,
                    output_dir=organize_dir,
                    metadata_dir=metadata_dir,
                    file_locator=file_locator,
                    output_locator=output_locator,
                    allow_local_output=allow_local_output,
                    link_mode=effective_link_mode,
                    source=ScrapeJobSource.WATCHER,
                )
                await scrape_service.create_job(job_create)
                completed.add(file.path)
            except Exception:
                logger.exception("创建监控刮削任务失败，将保留等待重试: %s", file.path)
        return completed

    def _row_to_folder(self, row) -> WatchedFolder:
        """Convert database row to WatchedFolder."""
        path = row["path"]
        path_is_p115 = is_p115_virtual_path(path)
        inferred_provider: WatcherProvider = "115" if path_is_p115 else "local"
        stored_provider = row["provider"] if "provider" in row.keys() else inferred_provider
        provider: WatcherProvider = inferred_provider
        if stored_provider not in {"local", "115"} or stored_provider != inferred_provider:
            logger.warning(
                "已修正旧监控目录的存储类型 path=%s provider=%s -> %s",
                path,
                stored_provider,
                inferred_provider,
            )
        else:
            provider = stored_provider

        mode_value = row["mode"] if "mode" in row.keys() else "realtime"
        try:
            mode = WatcherMode(mode_value)
        except ValueError:
            mode = WatcherMode.COMPAT
            logger.warning("已修正旧监控目录的无效模式 path=%s mode=%s", path, mode_value)
        if provider == "115" and mode == WatcherMode.REALTIME:
            mode = WatcherMode.COMPAT
        elif provider == "local" and mode == WatcherMode.EVENT:
            mode = WatcherMode.COMPAT

        output_dir = row["output_dir"] if "output_dir" in row.keys() else None
        if provider == "local" and output_dir and is_p115_virtual_path(output_dir):
            logger.warning("已忽略本地监控目录不支持的 115 输出目录: %s", output_dir)
            output_dir = None
        file_id = row["file_id"] if "file_id" in row.keys() else None
        return WatchedFolder(
            id=row["id"],
            path=path,
            enabled=bool(row["enabled"]),
            mode=mode,
            scan_interval_seconds=row["scan_interval_seconds"],
            file_stable_seconds=row["file_stable_seconds"],
            auto_scrape=bool(row["auto_scrape"]),
            output_dir=output_dir,
            provider=provider,
            file_id=file_id,
            last_scan=datetime.fromisoformat(row["last_scan"]) if row["last_scan"] else None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )
