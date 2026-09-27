"""历史记录的文件清单/物理删除与撤销删除。

覆盖的是本次新增的两件不可逆操作，因此断言的重点在**边界**而非正常路径：
只有登记过、在允许根目录内、并且确实是文件的路径才允许被删。
"""

import tempfile
from datetime import datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from server.application.history_actions import rebuild_metadata
from server.application.history_files import HistoryFileService
from server.application.history_service import HistoryService
from server.application.scraped_file_service import ScrapedFileService
from server.domain.system.config_service import ConfigService
from server.infrastructure.db import create_all_tables
from server.models.history import HistoryFileRole, HistoryRecordCreate, TaskStatus
from server.models.organize import OrganizeConfig
from server.models.scraped_file import ScrapedFileCreate


async def _initialize_test_db(db_path: Path) -> None:
    """建全套表：显式传 db_path 时仓储不会自建表，由测试负责。"""
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


def _make_record(history_service: HistoryService, folder_path: str, name: str = "测试记录"):
    return history_service.create_record(
        HistoryRecordCreate(
            task_name=name,
            folder_path=folder_path,
            status=TaskStatus.SUCCESS,
            total_files=1,
            success_count=1,
            failed_count=0,
            duration_seconds=1.0,
        )
    )


@pytest_asyncio.fixture
async def env(temp_db: Path):
    """临时库 + 临时目录：源目录 / 整理目录 / 元数据目录三分离。"""
    await _initialize_test_db(temp_db)
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "source"
        organize_dir = root / "organized"
        metadata_dir = root / "meta"
        for path in (source_dir, organize_dir, metadata_dir):
            path.mkdir()

        source_file = source_dir / "EP01.mkv"
        source_file.write_bytes(b"source-bytes")
        target_file = organize_dir / "剧集 - S01E01.mkv"
        target_file.write_bytes(b"organized-bytes")

        history_service = HistoryService(db_path=temp_db)
        scraped_file_service = ScrapedFileService(db_path=temp_db)
        config_service = ConfigService(db_path=temp_db)

        yield {
            "root": root,
            "source_dir": source_dir,
            "organize_dir": organize_dir,
            "metadata_dir": metadata_dir,
            "source_file": source_file,
            "target_file": target_file,
            "history": history_service,
            "scraped": scraped_file_service,
            "config": config_service,
        }


async def _register_scraped_file(env: dict, record_id: str) -> None:
    await env["scraped"].add_record(
        ScrapedFileCreate(
            source_path=str(env["source_file"]),
            target_path=str(env["target_file"]),
            file_size=env["source_file"].stat().st_size,
            tmdb_id=12345,
            season=1,
            episode=1,
            title="剧集",
            history_record_id=record_id,
        )
    )


def _service(env: dict) -> HistoryFileService:
    return HistoryFileService(env["history"], env["scraped"], env["config"])


@pytest.mark.asyncio
async def test_list_files_reports_source_and_organized(env):
    """登记过的源文件与产物都要列出来，且都在允许根目录内 → 可删。"""
    await env["config"].save_organize_config(
        OrganizeConfig(
            organize_dir=str(env["organize_dir"]),
            metadata_dir=str(env["metadata_dir"]),
        )
    )
    record = await _make_record(env["history"], str(env["source_file"]))
    await _register_scraped_file(env, record.id)

    listing = await _service(env).list_files(record.id)

    roles = {entry.role for entry in listing.files}
    assert roles == {HistoryFileRole.SOURCE, HistoryFileRole.ORGANIZED}
    assert all(entry.deletable for entry in listing.files)
    assert all(entry.exists for entry in listing.files)


@pytest.mark.asyncio
async def test_list_files_refuses_paths_outside_allowed_roots(env):
    """越界路径不可删——这是对库里脏数据的兜底，必须拒绝而不是默默删掉。"""
    await env["config"].save_organize_config(
        OrganizeConfig(organize_dir=str(env["organize_dir"]))
    )
    record = await _make_record(env["history"], str(env["source_file"]))

    outside = env["root"] / "elsewhere.mkv"
    outside.write_bytes(b"x")
    await env["scraped"].add_record(
        ScrapedFileCreate(
            source_path=str(outside),
            target_path=None,
            file_size=1,
            history_record_id=record.id,
        )
    )

    listing = await _service(env).list_files(record.id)
    entry = next(item for item in listing.files if item.path == str(outside))

    assert entry.deletable is False
    assert entry.reason == "不在允许的目录范围内"


@pytest.mark.asyncio
async def test_list_files_never_offers_a_directory(env):
    """目录绝不可删：没有 rmtree 就永远不会误删整棵目录树。"""
    record = await _make_record(env["history"], str(env["source_dir"]))
    await env["scraped"].add_record(
        ScrapedFileCreate(
            source_path=str(env["source_dir"]),
            target_path=None,
            file_size=0,
            history_record_id=record.id,
        )
    )

    listing = await _service(env).list_files(record.id)
    entry = listing.files[0]

    assert entry.exists is True
    assert entry.deletable is False
    assert entry.reason == "目录不会被删除"


@pytest.mark.asyncio
async def test_delete_organized_keeps_source_and_drops_registration(env):
    """删产物：源文件必须留着，登记行随文件一起清掉（否则再扫到会被当成已处理）。"""
    await env["config"].save_organize_config(
        OrganizeConfig(organize_dir=str(env["organize_dir"]))
    )
    record = await _make_record(env["history"], str(env["source_file"]))
    await _register_scraped_file(env, record.id)

    response = await _service(env).delete_files(record.id, "organized")

    assert response.deleted == 1
    assert response.failed == 0
    assert response.undoable is False  # 物理删除不可撤销，前端据此不显示撤销
    assert not env["target_file"].exists()
    assert env["source_file"].exists()
    assert await env["scraped"].list_by_history_record(record.id) == []


@pytest.mark.asyncio
async def test_delete_all_removes_both_files(env):
    """删源文件 + 产物：两个都消失。"""
    await env["config"].save_organize_config(
        OrganizeConfig(
            organize_dir=str(env["organize_dir"]),
            metadata_dir=str(env["metadata_dir"]),
        )
    )
    record = await _make_record(env["history"], str(env["source_file"]))
    await _register_scraped_file(env, record.id)

    response = await _service(env).delete_files(record.id, "all")

    assert response.deleted == 2
    assert not env["target_file"].exists()
    assert not env["source_file"].exists()


@pytest.mark.asyncio
async def test_delete_missing_file_is_reported_not_silently_ok(env):
    """文件早就不在了：回报原因，而不是假装删成功。"""
    record = await _make_record(env["history"], str(env["source_file"]))
    await env["scraped"].add_record(
        ScrapedFileCreate(
            source_path=str(env["source_dir"] / "gone.mkv"),
            target_path=None,
            file_size=0,
            history_record_id=record.id,
        )
    )

    response = await _service(env).delete_files(record.id, "source")

    assert response.deleted == 0
    assert response.failed == 1
    assert response.results[0].reason == "文件不存在"


# ---- 撤销 ----


@pytest.mark.asyncio
async def test_list_files_falls_back_to_folder_path_without_registration(env):
    """没有登记行的老记录：从 folder_path 的「源文件 => 产物」两侧拆出清单。

    登记功能上线前刮削的记录一条登记行都没有，此前弹窗只能显示「没有登记过任何文件」，
    用户无法删除已经落盘的产物。
    """
    await env["config"].save_organize_config(
        OrganizeConfig(organize_dir=str(env["organize_dir"]))
    )
    record = await _make_record(
        env["history"],
        f"{env['source_file']} => {env['target_file']}",
    )

    listing = await _service(env).list_files(record.id)

    assert {entry.role for entry in listing.files} == {
        HistoryFileRole.SOURCE,
        HistoryFileRole.ORGANIZED,
    }
    assert {entry.path for entry in listing.files} == {
        str(env["source_file"]),
        str(env["target_file"]),
    }
    # 源目录与产物目录都在允许范围内，两侧都能删
    assert all(entry.deletable for entry in listing.files)


@pytest.mark.asyncio
async def test_list_files_falls_back_to_single_source_file(env):
    """folder_path 没有产物侧时只当它是源文件（待处理/失败记录都是这个形态）。"""
    record = await _make_record(env["history"], str(env["source_file"]))

    listing = await _service(env).list_files(record.id)

    assert len(listing.files) == 1
    assert listing.files[0].role == HistoryFileRole.SOURCE
    assert listing.files[0].path == str(env["source_file"])


@pytest.mark.asyncio
async def test_register_output_records_size_and_is_idempotent(env):
    """登记产物：按源路径幂等，产物不存在时记 0 而不是写错数字。"""
    record = await _make_record(env["history"], str(env["source_file"]))
    payload = {
        "history_record_id": record.id,
        "source_path": str(env["source_file"]),
        "target_path": str(env["target_file"]),
        "tmdb_id": 12345,
        "season": 1,
        "episode": 1,
        "title": "剧集",
    }

    await env["scraped"].register_output(**payload)
    await env["scraped"].register_output(**payload)  # 重复登记不该多出一行

    rows = await env["scraped"].list_by_history_record(record.id)
    assert len(rows) == 1
    assert rows[0].file_size == env["target_file"].stat().st_size

    # 云端产物拿到的是 locator：stat 会失败，此时只能记 0
    await env["scraped"].register_output(
        **{**payload, "source_path": "cloud://another", "target_path": "115://locator"}
    )
    all_rows = await env["scraped"].list_by_history_record(record.id)
    cloud = next((row for row in all_rows if row.source_path == "cloud://another"), None)
    assert cloud is not None and cloud.file_size == 0


async def _make_organized_layout(env: dict, record_id: str) -> dict:
    """搭出与生产一致的目录形状并登记产物：整理/元数据两侧都是 <系列>/<季>/<文件>。

    生产里产物视频与元数据是并列的两棵树（整理目录、元数据目录），这里必须照抄，
    否则「本集/本季/本系列还剩几集」的判定拿不到真实目录结构。
    """
    series_name = "示例剧集 (2026)"
    season_name = "Season 1"
    series_dir = env["organize_dir"] / series_name
    season_dir = series_dir / season_name
    season_dir.mkdir(parents=True)
    target = season_dir / "示例剧集 - S01E01.mkv"
    target.write_bytes(b"organized-bytes")

    metadata_season_dir = env["metadata_dir"] / series_name / season_name
    metadata_season_dir.mkdir(parents=True)
    metadata_series_dir = env["metadata_dir"] / series_name

    await env["scraped"].add_record(
        ScrapedFileCreate(
            source_path=str(env["source_file"]),
            target_path=str(target),
            file_size=target.stat().st_size,
            tmdb_id=12345,
            season=1,
            episode=1,
            title="示例剧集",
            history_record_id=record_id,
        )
    )
    return {
        "target": target,
        "metadata_series_dir": metadata_series_dir,
        "metadata_season_dir": metadata_season_dir,
    }


@pytest.mark.asyncio
async def test_list_files_includes_episode_metadata_and_shared_files(env):
    """刮削产物要含元数据：本集 nfo/图片必列；单集时共享文件（season/tvshow/海报）也可删。"""
    await env["config"].save_organize_config(
        OrganizeConfig(
            organize_dir=str(env["organize_dir"]),
            metadata_dir=str(env["metadata_dir"]),
        )
    )
    record = await _make_record(env["history"], str(env["source_file"]))
    layout = await _make_organized_layout(env, record.id)

    stem = layout["target"].stem
    metadata_files = [
        layout["metadata_season_dir"] / f"{stem}.nfo",
        layout["metadata_season_dir"] / f"{stem}.jpg",
        layout["metadata_season_dir"] / "season.nfo",
        layout["metadata_series_dir"] / "tvshow.nfo",
        layout["metadata_series_dir"] / "poster.jpg",
        layout["metadata_series_dir"] / "backdrop.jpg",
    ]
    for path in metadata_files:
        path.write_bytes(b"meta")

    listing = await _service(env).list_files(record.id)
    roles = {entry.role for entry in listing.files}
    paths = {entry.path for entry in listing.files}

    assert roles == {
        HistoryFileRole.SOURCE,
        HistoryFileRole.ORGANIZED,
        HistoryFileRole.METADATA,
    }
    assert {str(path) for path in metadata_files} <= paths
    assert all(entry.deletable for entry in listing.files)


@pytest.mark.asyncio
async def test_shared_metadata_files_are_not_offered_when_another_episode_exists(env):
    """同系列还有其他集时，共享文件（season.nfo / tvshow.nfo / 海报）不能列出来被删。"""
    await env["config"].save_organize_config(
        OrganizeConfig(
            organize_dir=str(env["organize_dir"]),
            metadata_dir=str(env["metadata_dir"]),
        )
    )
    record = await _make_record(env["history"], str(env["source_file"]))
    layout = await _make_organized_layout(env, record.id)

    stem = layout["target"].stem
    episode_nfo = layout["metadata_season_dir"] / f"{stem}.nfo"
    episode_nfo.write_bytes(b"meta")
    season_nfo = layout["metadata_season_dir"] / "season.nfo"
    season_nfo.write_bytes(b"shared")
    poster = layout["metadata_series_dir"] / "poster.jpg"
    poster.write_bytes(b"shared")
    # 同系列的另一集（元数据侧还留着它的 nfo）：共享文件必须留着
    other_episode = layout["metadata_season_dir"] / "示例剧集 - S01E02.nfo"
    other_episode.write_bytes(b"other")

    listing = await _service(env).list_files(record.id)
    paths = {entry.path for entry in listing.files}

    assert str(episode_nfo) in paths
    assert str(season_nfo) not in paths
    assert str(poster) not in paths

    # 另一集没了，共享文件才变成可列可删
    other_episode.unlink()
    listing = await _service(env).list_files(record.id)
    paths = {entry.path for entry in listing.files}
    assert str(season_nfo) in paths
    assert str(poster) in paths


@pytest.mark.asyncio
async def test_delete_organized_scope_covers_metadata(env):
    """选「刮削产物」时元数据也要一起删（scope=organized 覆盖 metadata 角色）。"""
    await env["config"].save_organize_config(
        OrganizeConfig(
            organize_dir=str(env["organize_dir"]),
            metadata_dir=str(env["metadata_dir"]),
        )
    )
    record = await _make_record(env["history"], str(env["source_file"]))
    layout = await _make_organized_layout(env, record.id)

    episode_nfo = layout["metadata_season_dir"] / f"{layout['target'].stem}.nfo"
    episode_nfo.write_bytes(b"meta")

    response = await _service(env).delete_files(record.id, "organized")

    assert response.deleted == 2  # 视频 + nfo
    assert response.failed == 0
    assert layout["target"].exists() is False
    assert episode_nfo.exists() is False
    assert env["source_file"].exists() is True  # 源文件留着


@pytest.mark.asyncio
async def test_undo_restores_deleted_record_with_its_files(env):
    """撤销删除：记录行与登记行都要回来，且只能撤销一次。"""
    record = await _make_record(env["history"], str(env["source_file"]))
    await _register_scraped_file(env, record.id)

    assert await env["history"].delete_record(record.id) is True
    deleted = await env["history"].get_record(record.id)
    assert deleted is not None
    assert deleted.status == TaskStatus.DELETED

    undone = await env["history"].undo_last()

    assert undone.undone is True
    assert undone.restored == 1
    restored = await env["history"].get_record(record.id)
    assert restored is not None
    assert restored.id == record.id
    assert restored.display_id == record.display_id  # 序号不能被重排
    assert len(await env["scraped"].list_by_history_record(record.id)) == 1

    # 只保留最近一次：再撤就是没有可撤
    assert (await env["history"].undo_last()).undone is False


@pytest.mark.asyncio
async def test_undo_restores_cleared_records(env):
    """清空历史：一次撤销要把整批恢复回来。"""
    ids = []
    for index in range(3):
        record = await _make_record(
            env["history"], str(env["source_file"]), name=f"记录{index}"
        )
        ids.append(record.id)

    assert await env["history"].clear_records() == 3
    assert (await env["history"].list_records(limit=10))[0] == []

    undone = await env["history"].undo_last()

    assert undone.restored == 3
    records, total = await env["history"].list_records(limit=10)
    assert total == 3
    assert {row.id for row in records} == set(ids)


@pytest.mark.asyncio
async def test_undo_without_snapshot_is_a_noop(env):
    """没有快照时不能报错——前端提示条可能被重复点击。"""
    response = await env["history"].undo_last()

    assert response.success is False
    assert response.undone is False
    assert response.message == "没有可撤销的操作"


# ---- 重新整理用的元数据重建 ----


@pytest.mark.asyncio
async def test_rebuild_metadata_from_stored_fields(env):
    """重新整理用的模型只能来自存档，图片地址要还原成 TMDB path。"""
    record = await _make_record(env["history"], str(env["source_file"]))
    await env["history"].update_record(
        record.id,
        title="剧集名",
        original_title="Original",
        plot="简介",
        poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
        release_date="2024-04-01",
        rating=7.5,
        tags=["动画"],
        episode_title="第一集",
        episode_still_url="https://image.tmdb.org/t/p/w500/still.jpg",
        episode_air_date="2024-04-02",
    )
    detail = await env["history"].get_record(record.id)

    series, season = rebuild_metadata(
        detail, tmdb_id=999, season_num=1, episode_num=2
    )

    assert series.id == 999
    assert series.name == "剧集名"
    assert series.poster_path == "poster.jpg"  # 尺寸段被剥掉
    assert series.first_air_date == datetime(2024, 4, 1).date()
    assert series.genres == ["动画"]
    assert season.season_number == 1
    assert season.episodes[0].episode_number == 2
    assert season.episodes[0].name == "第一集"
    assert season.episodes[0].still_path == "still.jpg"
