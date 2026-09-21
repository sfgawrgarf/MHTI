"""Scraped-file upsert regressions."""

import aiosqlite
import pytest

from server.core.db import configure_connection, create_all_tables
from server.models.scraped_file import ScrapedFileCreate
from server.services.scraped_file_service import ScrapedFileService


@pytest.mark.asyncio
async def test_upsert_returns_the_persisted_record_id(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()
    service = ScrapedFileService(temp_db)

    first = await service.add_record(
        ScrapedFileCreate(source_path="/media/show.mkv", file_size=100)
    )
    updated = await service.add_record(
        ScrapedFileCreate(
            source_path="/media/show.mkv",
            target_path="/library/show.strm",
            file_size=200,
        )
    )

    assert updated.id == first.id
    assert updated.target_path == "/library/show.strm"
    assert updated.file_size == 200
