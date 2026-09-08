"""Scraped file service - 已刮削文件记录服务"""

import logging
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from server.core.db.connection import db_connection
from server.core.database import DATABASE_PATH
from server.models.scraped_file import ScrapedFile, ScrapedFileCreate

logger = logging.getLogger(__name__)


class ScrapedFileService:
    """已刮削文件记录服务"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATABASE_PATH

    async def _ensure_db(self) -> None:
        """确保数据库目录存在（表由 db 模块创建）"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def add_record(self, data: ScrapedFileCreate) -> ScrapedFile:
        """添加已刮削文件记录（如果已存在则更新）"""
        await self._ensure_db()

        record_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        async with db_connection(self.db_path) as db:
            # 使用 INSERT OR REPLACE 实现 upsert
            await db.execute(
                """
                INSERT OR REPLACE INTO scraped_files
                (id, source_path, target_path, file_size, tmdb_id, season, episode, title, scraped_at, history_record_id)
                VALUES (
                    COALESCE((SELECT id FROM scraped_files WHERE source_path = ?), ?),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    data.source_path, record_id,
                    data.source_path, data.target_path, data.file_size,
                    data.tmdb_id, data.season, data.episode, data.title,
                    now.isoformat(), data.history_record_id,
                ),
            )
            await db.commit()

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

    async def is_scraped(self, source_path: str) -> bool:
        """检查文件是否已刮削"""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM scraped_files WHERE source_path = ? LIMIT 1",
                (source_path,),
            )
            row = await cursor.fetchone()

        return row is not None

    async def get_scraped_paths(self, paths: list[str]) -> set[str]:
        """批量检查哪些文件已刮削，返回已刮削的路径集合"""
        if not paths:
            return set()

        await self._ensure_db()

        placeholders = ",".join("?" * len(paths))
        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                f"SELECT source_path FROM scraped_files WHERE source_path IN ({placeholders})",
                paths,
            )
            rows = await cursor.fetchall()

        return {row[0] for row in rows}

    async def get_record(self, source_path: str) -> ScrapedFile | None:
        """根据源路径获取记录"""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM scraped_files WHERE source_path = ?",
                (source_path,),
            )
            row = await cursor.fetchone()

        return self._row_to_record(row) if row else None

    async def list_records(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> tuple[list[ScrapedFile], int]:
        """列出已刮削文件记录"""
        await self._ensure_db()

        conditions = []
        params: list = []

        if search:
            conditions.append("(source_path LIKE ? OR title LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                f"SELECT COUNT(*) as count FROM scraped_files {where_clause}",
                params,
            )
            row = await cursor.fetchone()
            total = row["count"] if row else 0

            cursor = await db.execute(
                f"""
                SELECT * FROM scraped_files {where_clause}
                ORDER BY scraped_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            rows = await cursor.fetchall()

        records = [self._row_to_record(row) for row in rows]
        return records, total

    async def delete_records(self, ids: list[str]) -> int:
        """删除记录（允许文件重新刮削）"""
        if not ids:
            return 0

        await self._ensure_db()

        placeholders = ",".join("?" * len(ids))
        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                f"DELETE FROM scraped_files WHERE id IN ({placeholders})",
                ids,
            )
            await db.commit()
            return cursor.rowcount

    async def delete_by_paths(self, paths: list[str]) -> int:
        """根据源路径删除记录"""
        if not paths:
            return 0

        await self._ensure_db()

        placeholders = ",".join("?" * len(paths))
        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                f"DELETE FROM scraped_files WHERE source_path IN ({placeholders})",
                paths,
            )
            await db.commit()
            return cursor.rowcount

    async def clear_all(self) -> int:
        """清空所有记录"""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            cursor = await db.execute("DELETE FROM scraped_files")
            await db.commit()
            return cursor.rowcount

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
