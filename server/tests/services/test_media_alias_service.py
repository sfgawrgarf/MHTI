import json

import aiosqlite
import pytest

from server.core.db.schema import create_all_tables
from server.services.media_alias_service import MediaAliasService


@pytest.mark.asyncio
async def test_confirmed_release_alias_round_trip(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await create_all_tables(db)
        await db.commit()
    service = MediaAliasService(temp_db)

    inserted = await service.remember_confirmed(
        file_path="/incoming/[Maho.sub]作品 第二夜.strm",
        parsed_title="作品",
        tmdb_id=123,
        season=1,
        episode=2,
        canonical_titles=["作品", "Work"],
    )
    match = await service.lookup(
        file_path="/incoming/[Maho.sub]作品 第二夜.strm",
        parsed_title="作品",
    )

    assert inserted >= 2
    assert match is not None
    assert match.alias_type == "release"
    assert (match.tmdb_id, match.season, match.episode) == (123, 1, 2)


@pytest.mark.asyncio
async def test_conflicting_confirmed_alias_is_disabled(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await create_all_tables(db)
        await db.commit()
    service = MediaAliasService(temp_db)
    kwargs = {
        "file_path": "/incoming/作品 第二夜.strm",
        "parsed_title": "作品",
        "season": 1,
        "episode": 2,
        "canonical_titles": ["作品"],
    }

    await service.remember_confirmed(tmdb_id=123, **kwargs)
    await service.remember_confirmed(tmdb_id=456, **kwargs)
    match = await service.lookup(
        file_path=kwargs["file_path"],
        parsed_title=kwargs["parsed_title"],
    )

    assert match is None


@pytest.mark.asyncio
async def test_ambiguous_series_alias_does_not_override_exact_releases(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await create_all_tables(db)
        await db.commit()
    service = MediaAliasService(temp_db)

    await service.remember_confirmed(
        file_path="/incoming/作品 第一夜.strm",
        parsed_title="作品",
        tmdb_id=123,
        season=1,
        episode=1,
        canonical_titles=["作品"],
    )
    await service.remember_confirmed(
        file_path="/incoming/作品 第二夜.strm",
        parsed_title="作品",
        tmdb_id=456,
        season=1,
        episode=2,
        canonical_titles=["作品"],
    )

    exact_first = await service.lookup(
        file_path="/incoming/作品 第一夜.strm",
        parsed_title="作品",
    )
    unknown_release = await service.lookup(
        file_path="/incoming/作品 第三夜.strm",
        parsed_title="作品",
    )

    assert exact_first is not None
    assert exact_first.tmdb_id == 123
    assert unknown_release is None


@pytest.mark.asyncio
async def test_backfill_learns_manual_history_mapping(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await create_all_tables(db)
        await db.execute(
            """
            INSERT INTO history_records (
                id, task_name, folder_path, executed_at, status,
                total_files, success_count, failed_count, duration_seconds,
                title, original_title
            ) VALUES ('h1', 'manual', '/incoming/作品 第二夜.strm',
                      '2026-01-01', 'success', 1, 1, 0, 1.0,
                      '中文作品', '作品')
            """
        )
        logs = [
            {
                "name": "解析文件名",
                "logs": [
                    {"message": "视频文件路径: /incoming/作品 第二夜.strm"},
                    {"message": "解析结果: 作品 S1E2"},
                ],
            },
            {
                "name": "用户手动选择",
                "logs": [
                    {"message": "用户手动输入 TMDB ID: 123, S01E02"},
                ],
            },
        ]
        await db.execute(
            "UPDATE history_records SET scrape_logs = ? WHERE id = 'h1'",
            (json.dumps(logs, ensure_ascii=False),),
        )
        await db.commit()

    service = MediaAliasService(temp_db)
    learned = await service.backfill_confirmed_history()
    match = await service.lookup(
        file_path="/incoming/作品 第二夜.strm",
        parsed_title="作品",
    )

    assert learned >= 2
    assert match is not None
    assert (match.tmdb_id, match.season, match.episode) == (123, 1, 2)
