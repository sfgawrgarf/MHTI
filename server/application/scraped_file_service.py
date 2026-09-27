"""Scraped file service - 已刮削文件记录服务"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.scraped_file_repository import ScrapedFileRepository
from server.models.scraped_file import ScrapedFile, ScrapedFileCreate

logger = logging.getLogger(__name__)


class ScrapedFileService:
    """已刮削文件记录服务"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATABASE_PATH
        self._repo = ScrapedFileRepository(db_path)

    async def add_record(self, data: ScrapedFileCreate) -> ScrapedFile:
        """添加已刮削文件记录（如果已存在则更新）"""
        record_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        await self._repo.upsert_record(
            record_id=record_id,
            source_path=data.source_path,
            target_path=data.target_path,
            file_size=data.file_size,
            tmdb_id=data.tmdb_id,
            season=data.season,
            episode=data.episode,
            title=data.title,
            scraped_at=now.isoformat(),
            history_record_id=data.history_record_id,
        )

        return ScrapedFile(
            id=record_id,
            source_path=data.source_path,
            target_path=data.target_path,
            file_size=data.file_size,
            tmdb_id=data.tmdb_id,
            season=data.season,
            episode=data.episode,
            title=data.title,
            scraped_at=now,
            history_record_id=data.history_record_id,
        )

    async def register_output(
        self,
        *,
        history_record_id: str,
        source_path: str | None,
        target_path: str | None,
        tmdb_id: int | None,
        season: int | None,
        episode: int | None,
        title: str | None,
        replace_existing: bool = False,
    ) -> None:
        """登记一次成功刮削的产物（按源路径幂等，重复调用只会刷新同一行）。

        刮削成功与否由调用方决定，登记只是附带动作：没有产物路径就不登记；
        产物路径在云端（115 等）时拿到的是 locator 而不是本地路径，stat 会失败，
        此时只能记 0（「已刮削文件」页显示为未知，好过写一个错数字）。

        replace_existing=True 供重刮使用：按历史记录定位原行就地更新（id 不变），
        避免换路径后多出一条登记行。
        """
        if not target_path:
            return
        try:
            file_size = os.path.getsize(target_path)
        except OSError:
            file_size = 0

        if replace_existing:
            updated = await self._repo.update_by_history_record(
                history_record_id=history_record_id,
                source_path=source_path or "",
                target_path=target_path,
                file_size=file_size,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                title=title,
                scraped_at=datetime.now().isoformat(),
            )
            if updated:
                return

        await self.add_record(
            ScrapedFileCreate(
                source_path=source_path or "",
                target_path=target_path,
                file_size=file_size,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                title=title,
                history_record_id=history_record_id,
            )
        )

    async def list_by_history_record(self, history_record_id: str) -> list[ScrapedFile]:
        """列出某条历史记录登记的文件（删除与重新整理的路径依据）。"""
        rows = await self._repo.list_by_history_record(history_record_id)
        return [self._row_to_record(row) for row in rows]

    async def is_scraped(self, source_path: str) -> bool:
        """Check whether a source path already has a registration."""
        return await self._repo.get_by_source_path(source_path) is not None

    async def get_record(self, source_path: str) -> ScrapedFile | None:
        """Return a registration by source path."""
        row = await self._repo.get_by_source_path(source_path)
        return self._row_to_record(row) if row is not None else None

    async def list_records(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> tuple[list[ScrapedFile], int]:
        """List registrations for the administration UI."""
        rows, total = await self._repo.list_records(
            limit=limit,
            offset=offset,
            search=search,
        )
        return [self._row_to_record(row) for row in rows], total

    async def delete_records(self, ids: list[str]) -> int:
        """Delete registrations by ID so a source can be scraped again."""
        return await self._repo.delete_records(ids)

    async def delete_by_paths(self, paths: list[str]) -> int:
        """Delete registrations matching either source or output paths."""
        return await self._repo.delete_by_any_paths(paths)

    async def clear_all(self) -> int:
        """Clear all registrations."""
        return await self._repo.clear_all()

    async def delete_by_any_paths(self, paths: list[str]) -> int:
        """按源路径或产物路径删除记录（物理删除文件后清登记用）。"""
        return await self._repo.delete_by_any_paths(paths)

    def _row_to_record(self, row) -> ScrapedFile:
        """转换数据库行到模型"""
        return ScrapedFile(
            id=row["id"],
            source_path=row["source_path"],
            target_path=row["target_path"],
            file_size=row["file_size"],
            tmdb_id=row["tmdb_id"],
            season=row["season"],
            episode=row["episode"],
            title=row["title"],
            scraped_at=datetime.fromisoformat(row["scraped_at"]),
            history_record_id=row["history_record_id"],
        )
