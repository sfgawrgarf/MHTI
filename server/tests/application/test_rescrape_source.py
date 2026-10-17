"""重刮/重新整理的输入解析、旧产物清理与登记就地更新。

这一批改动的边界都在「用户点重刮那一刻」：记录里存的 folder_path 是
`源文件 => 产物` 一整串，拿它当路径必然失败，所以断言集中在四件事——

1. 输入优先级：源文件 > 记录 folder_path 的源侧 > 产物；
2. 源文件没了要能用产物跑，且产物必须先让出原路径（否则自己和自己冲突）；
3. 让位文件成功就删、失败就放回原位；
4. 登记行不新开一条（重刮换路径后仍是一行、id 不变）。
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import HTTPException

from server.application.history_actions import HistoryScrapeActions
from server.application.history_files import HistoryFileService
from server.application.history_service import HistoryService
from server.application.rescrape_source import RescrapeSource
from server.application.scrape_job_service import ScrapeJobService
from server.application.scraped_file_service import ScrapedFileService
from server.domain.system.config_service import ConfigService
from server.infrastructure.db import create_all_tables
from server.models.history import HistoryRecordCreate, TaskStatus
from server.models.organize import OrganizeConfig
from server.models.scraped_file import ScrapedFileCreate


async def _initialize_test_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


@pytest_asyncio.fixture
async def env(temp_db: Path):
    """临时库 + 源目录/整理目录/元数据目录三分离，形状与生产一致。"""
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

        series_name = "示例剧集 (2026)"
        season_name = "Season 1"
        season_dir = organize_dir / series_name / season_name
        season_dir.mkdir(parents=True)
        target_file = season_dir / "示例剧集 - S01E01.mkv"
        target_file.write_bytes(b"organized-bytes")

        metadata_season_dir = metadata_dir / series_name / season_name
        metadata_season_dir.mkdir(parents=True)
        episode_nfo = metadata_season_dir / f"{target_file.stem}.nfo"
        episode_nfo.write_bytes(b"nfo")

        history = HistoryService(db_path=temp_db)
        scraped = ScrapedFileService(db_path=temp_db)
        config = ConfigService(db_path=temp_db)
        await config.save_organize_config(
            OrganizeConfig(organize_dir=str(organize_dir), metadata_dir=str(metadata_dir))
        )

        yield {
            "root": root,
            "source_file": source_file,
            "target_file": target_file,
            "episode_nfo": episode_nfo,
            "history": history,
            "scraped": scraped,
            "config": config,
            "files": HistoryFileService(history, scraped, config),
        }


async def _make_record(env: dict, folder_path: str) -> object:
    return await env["history"].create_record(
        HistoryRecordCreate(
            task_name="重刮用例",
            folder_path=folder_path,
            status=TaskStatus.SUCCESS,
            total_files=1,
            success_count=1,
            failed_count=0,
            duration_seconds=1.0,
        )
    )


async def _register(env: dict, record_id: str, *, source: str | None = None, target: str | None = None):
    return await env["scraped"].add_record(
        ScrapedFileCreate(
            source_path=source if source is not None else str(env["source_file"]),
            target_path=target if target is not None else str(env["target_file"]),
            file_size=1,
            tmdb_id=12345,
            season=1,
            episode=1,
            title="示例剧集",
            history_record_id=record_id,
        )
    )


def _resolver(env: dict) -> RescrapeSource:
    return RescrapeSource(env["scraped"], env["files"])


# ---- 输入解析 ----


@pytest.mark.asyncio
async def test_resolve_prefers_source_file(env):
    """源文件与产物都在时，重刮必须用源文件。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)

    resolved = await _resolver(env).resolve(record)

    assert resolved.path == str(env["source_file"])
    assert resolved.from_product is False


@pytest.mark.asyncio
async def test_resolve_falls_back_to_product_when_source_is_gone(env):
    """源文件被删（用户清硬盘）时改用整理后的产物，并标记来源。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)
    env["source_file"].unlink()

    resolved = await _resolver(env).resolve(record)

    assert resolved.path == str(env["target_file"])
    assert resolved.from_product is True


@pytest.mark.asyncio
async def test_resolve_reads_source_side_of_folder_path_without_registration(env):
    """老记录没有登记行：源侧与产物侧都从 folder_path 里拆，且源侧优先。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )

    resolved = await _resolver(env).resolve(record)

    assert resolved.path == str(env["source_file"])
    assert resolved.from_product is False

    env["source_file"].unlink()
    fallback = await _resolver(env).resolve(record)
    assert fallback.path == str(env["target_file"])
    assert fallback.from_product is True


@pytest.mark.asyncio
async def test_resolve_raises_when_source_and_product_are_both_missing(env):
    """两侧都没了要直接 400（而不是把记录改成 failed 再报文件不存在）。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)
    env["source_file"].unlink()
    env["target_file"].unlink()

    with pytest.raises(HTTPException) as exc:
        await _resolver(env).resolve(record)

    assert exc.value.status_code == 400
    assert exc.value.detail == "源文件与产物都不存在，无法重刮"


@pytest.mark.asyncio
async def test_resolve_skips_existence_check_for_remote_input(env):
    """115 等云端的路径在本地不存在，remote=True 时不能按本地存在性否决。"""
    record = await _make_record(env, "115://源文件.mkv")
    await _register(env, record.id, source="115://源文件.mkv", target="115://产物.mkv")

    resolved = await _resolver(env).resolve(record, remote=True)

    assert resolved.path == "115://源文件.mkv"
    assert resolved.from_product is False


# ---- 让位与收尾 ----


@pytest.mark.asyncio
async def test_stage_moves_product_aside_and_success_deletes_it(env):
    """产物作为输入时先让出原路径（否则会和自己冲突），跑成功后删掉这份旧副本。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)
    env["source_file"].unlink()

    resolver = _resolver(env)
    resolved = await resolver.resolve(record)
    staged = await resolver.stage(resolved)

    assert staged.from_product is True
    assert staged.staged_from == str(env["target_file"])
    assert env["target_file"].exists() is False
    assert Path(staged.path).exists() is True
    assert Path(staged.path).name.startswith(env["target_file"].stem)

    await resolver.release(staged, dest_path=str(env["target_file"]), success=True)

    assert Path(staged.path).exists() is False
    assert env["target_file"].exists() is False  # 新产物由刮削流程写，测试里没有


@pytest.mark.asyncio
async def test_release_restores_product_when_run_failed(env):
    """跑失败必须把让位文件放回原位，磁盘上不能少一个文件、也不能留临时文件。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)
    env["source_file"].unlink()

    resolver = _resolver(env)
    staged = await resolver.stage(await resolver.resolve(record))
    await resolver.release(staged, dest_path=None, success=False)

    assert env["target_file"].exists() is True
    assert env["target_file"].read_bytes() == b"organized-bytes"
    assert list(env["target_file"].parent.glob("*mhti-rescrape*")) == []


@pytest.mark.asyncio
async def test_stage_is_a_noop_for_source_input(env):
    """源文件输入不需要让位——它和产物路径本来就不同。"""
    record = await _make_record(env, str(env["source_file"]))
    resolver = _resolver(env)

    staged = await resolver.stage(await resolver.resolve(record))

    assert staged.staged_from is None
    assert staged.path == str(env["source_file"])
    assert env["source_file"].exists() is True


@pytest.mark.asyncio
async def test_staged_file_survives_when_product_is_a_symlink_to_it(env):
    """软链接产物指向让位文件时不能删让位文件，否则产物变死链接。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)
    env["source_file"].unlink()
    resolver = _resolver(env)
    staged = await resolver.stage(await resolver.resolve(record))

    new_dest = env["target_file"].parent / "示例剧集 - S01E02.mkv"
    try:
        os.symlink(staged.path, new_dest)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建软链接")

    await resolver.release(staged, dest_path=str(new_dest), success=True)

    assert Path(staged.path).exists() is True  # 让位文件被保留
    new_dest.unlink()


# ---- 旧产物清理 ----


@pytest.mark.asyncio
async def test_clear_products_deletes_outputs_but_keeps_input_and_registration(env):
    """清旧产物：视频与集专属元数据都要删；输入文件与登记行必须留着（重刮要就地更新）。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    row = await _register(env, record.id)

    deleted = await _resolver(env).clear_products(record.id, keep_paths={str(env["source_file"])})

    assert set(deleted) == {str(env["target_file"]), str(env["episode_nfo"])}
    assert env["source_file"].exists() is True
    assert env["target_file"].exists() is False
    assert env["episode_nfo"].exists() is False

    rows = await env["scraped"].list_by_history_record(record.id)
    assert len(rows) == 1
    assert rows[0].id == row.id  # 登记行不能被清理动作删掉


@pytest.mark.asyncio
async def test_clear_products_never_deletes_the_input_itself(env):
    """产物就是本次输入时，即使没让位成功也不能把它删掉。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)
    env["source_file"].unlink()

    deleted = await _resolver(env).clear_products(record.id, keep_paths={str(env["target_file"])})

    # 输入那个视频必须留着（它是唯一的副本），元数据可以照常清掉
    assert str(env["target_file"]) not in deleted
    assert env["target_file"].exists() is True


# ---- 登记就地更新 ----


@pytest.mark.asyncio
async def test_replace_existing_registration_updates_same_row(env):
    """重刮换产物路径：登记行仍是一行、id 不变、新路径写入，源路径保留原值。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    row = await _register(env, record.id)
    new_target = env["target_file"].parent / "示例剧集 - S01E03.mkv"
    new_target.write_bytes(b"new-bytes")

    await env["scraped"].register_output(
        history_record_id=record.id,
        source_path="",  # 产物作为输入时不覆盖登记行的源文件
        target_path=str(new_target),
        tmdb_id=999,
        season=1,
        episode=3,
        title="新剧集",
        replace_existing=True,
    )

    rows = await env["scraped"].list_by_history_record(record.id)
    assert len(rows) == 1
    assert rows[0].id == row.id
    assert rows[0].source_path == str(env["source_file"])
    assert rows[0].target_path == str(new_target)
    assert (rows[0].tmdb_id, rows[0].season, rows[0].episode) == (999, 1, 3)
    assert rows[0].title == "新剧集"
    assert rows[0].file_size == new_target.stat().st_size


@pytest.mark.asyncio
async def test_replace_existing_registration_inserts_when_record_has_no_row(env):
    """没有登记行的老记录重刮后要补一行，而不是静默不登记。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )

    await env["scraped"].register_output(
        history_record_id=record.id,
        source_path=str(env["source_file"]),
        target_path=str(env["target_file"]),
        tmdb_id=12345,
        season=1,
        episode=1,
        title="示例剧集",
        replace_existing=True,
    )

    rows = await env["scraped"].list_by_history_record(record.id)
    assert len(rows) == 1
    assert rows[0].target_path == str(env["target_file"])


# ---- 用例层编排 ----


def _actions(env: dict) -> HistoryScrapeActions:
    return HistoryScrapeActions(
        env["history"],
        ScrapeJobService(db_path=env["history"].db_path),
        env["scraped"],
        env["files"],
    )


@pytest.mark.asyncio
async def test_prepare_input_reports_source_and_cleans_products(env):
    """用例层：源文件可用时按源文件跑，旧产物清掉，日志步骤写给用户看。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)

    prepared, steps, registration_source = await _actions(env).prepare_input(record)

    assert prepared.path == str(env["source_file"])
    assert registration_source == str(env["source_file"])
    assert env["target_file"].exists() is False
    assert [step.name for step in steps] == ["准备输入", "清理旧产物"]
    assert any("已删除旧产物" in entry.message for entry in steps[1].logs)


@pytest.mark.asyncio
async def test_prepare_input_uses_product_and_keeps_registration_source(env):
    """用例层：源文件没了→改用产物（已让位）、登记源路径留空（保留原行身份）。"""
    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    await _register(env, record.id)
    env["source_file"].unlink()

    prepared, steps, registration_source = await _actions(env).prepare_input(record)

    assert prepared.from_product is True
    assert prepared.staged_from == str(env["target_file"])
    assert registration_source == ""
    assert prepared.path != str(env["target_file"])
    assert Path(prepared.path).exists() is True
    assert any("改用整理后的产物" in entry.message for entry in steps[0].logs)
    # 让位后的路径不能被当成旧产物删掉，只留下元数据这一项
    assert env["episode_nfo"].exists() is False
    assert Path(prepared.path).exists() is True


# ---- 输出参数补齐 ----


async def _make_record_with_job(env: dict, job_id: str = "job00001") -> object:
    """建一条带任务行的记录：扫描流程创建的任务参数（整理目录/元数据目录/整理模式）在 job 上。

    直接插任务行而不是走 ScrapeJobService.create_job：后者会把 job 塞进队列并拉起常驻
    worker，测试进程会因此挂着不退出（实测）。
    """
    from server.infrastructure.repositories.scrape_job_repository import ScrapeJobRepository
    from server.models.organize import OrganizeMode
    from server.models.scrape_job import ScrapeJobCreate

    await ScrapeJobRepository(env["history"].db_path).insert_job(
        job_id,
        ScrapeJobCreate(
            file_path=str(env["source_file"]),
            output_dir=str(env["root"] / "organized"),
            metadata_dir=str(env["root"] / "meta"),
            link_mode=OrganizeMode.COPY,
        ),
        created_at=datetime.now().isoformat(),
        advanced_settings_json=None,
        file_locator_json=None,
        output_locator_json=None,
        metadata_locator_json=None,
    )
    return await env["history"].create_record(
        HistoryRecordCreate(
            task_name="重刮用例",
            folder_path=f"{env['source_file']} => {env['target_file']}",
            status=TaskStatus.SUCCESS,
            total_files=1,
            success_count=1,
            failed_count=0,
            duration_seconds=1.0,
            scrape_job_id=job_id,
        )
    )


def _request(**overrides):
    from server.models.scraper import ScrapeByIdRequest

    payload = {
        "file_path": "x.mkv",
        "tmdb_id": 1,
        "season": 1,
        "episode": 1,
    }
    payload.update(overrides)
    return ScrapeByIdRequest(**payload)


@pytest.mark.asyncio
async def test_fill_organize_params_reads_job_row(env):
    """conflict_data 没有输出目录时从任务行取——否则整理会退化成「原地重命名」。"""
    from server.models.organize import OrganizeMode

    record = await _make_record_with_job(env)
    request = _request()

    await _actions(env).fill_organize_params(record, request)

    assert request.output_dir == str(env["root"] / "organized")
    assert request.metadata_dir == str(env["root"] / "meta")
    assert request.link_mode == OrganizeMode.COPY


@pytest.mark.asyncio
async def test_fill_organize_params_keeps_explicit_values(env):
    """调用方（冲突处理分支）已经指定目录时不能被任务行覆盖。"""
    from server.models.organize import OrganizeMode

    record = await _make_record_with_job(env)
    request = _request(
        output_dir="D:/explicit-out",
        metadata_dir="D:/explicit-meta",
        link_mode=OrganizeMode.MOVE,
    )

    await _actions(env).fill_organize_params(record, request)

    assert request.output_dir == "D:/explicit-out"
    assert request.metadata_dir == "D:/explicit-meta"
    assert request.link_mode == OrganizeMode.MOVE


@pytest.mark.asyncio
async def test_fill_organize_params_without_job_is_a_noop(env):
    """老记录没有任务行：保持原样（None 仍由整理流程按既有默认值处理）。"""
    record = await _make_record(env, str(env["source_file"]))
    request = _request()

    await _actions(env).fill_organize_params(record, request)

    assert request.output_dir is None
    assert request.metadata_dir is None
    assert request.link_mode is None


# ---- 用例层端到端（刮削器打成桩，不联网） ----


class _RecordingScraper:
    """替身刮削器：记录收到的请求，并把输入文件复制到规划好的产物路径。"""

    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self.requests: list = []

    async def scrape_by_id(self, request, on_log_update=None):
        from server.models.scraper import ScrapeResult, ScrapeStatus

        self.requests.append(request)
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        self.dest.write_bytes(Path(request.file_path).read_bytes())
        if on_log_update:
            await on_log_update([])
        return ScrapeResult(
            file_path=request.file_path,
            status=ScrapeStatus.SUCCESS,
            selected_id=request.tmdb_id,
            parsed_season=request.season,
            parsed_episode=request.episode,
            dest_path=str(self.dest),
            message="刮削完成",
        )


def _patch_scraper(monkeypatch, scraper) -> None:
    import server.bootstrap

    monkeypatch.setattr(server.bootstrap, "get_scraper_service", lambda: scraper)


@pytest.mark.asyncio
async def test_execute_scrape_and_update_uses_source_then_rewrites_record(env, monkeypatch):
    """端到端（刮削器打桩）：用源文件跑、旧产物先清、成功后同一条记录与登记行就地更新。"""
    from server.models.scraper import ScrapeByIdRequest

    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    row = await _register(env, record.id)
    new_dest = env["target_file"].parent / "示例剧集 - S01E07.mkv"
    scraper = _RecordingScraper(new_dest)
    _patch_scraper(monkeypatch, scraper)

    result = await _actions(env).execute_scrape_and_update(
        record.id,
        ScrapeByIdRequest(file_path=record.folder_path, tmdb_id=12345, season=1, episode=7),
        "用户手动重试",
        resolve_input=True,
    )

    assert result["success"] is True
    # 1. 传给刮削器的是源文件，不是那串「源 => 产物」
    assert scraper.requests[0].file_path == str(env["source_file"])
    # 2. 旧产物（视频 + 元数据）先被清掉，新产物落到目标
    assert env["target_file"].exists() is False
    assert env["episode_nfo"].exists() is False
    assert new_dest.exists() is True
    assert env["source_file"].exists() is True

    # 3. 记录不新开一条：folder_path 刷新为本次「输入 => 产物」
    updated = await env["history"].get_record(record.id)
    assert updated.folder_path == f"{env['source_file']} => {new_dest}"

    # 4. 登记行就地更新：仍是一行、id 不变、产物路径是新的
    rows = await env["scraped"].list_by_history_record(record.id)
    assert len(rows) == 1
    assert rows[0].id == row.id
    assert rows[0].target_path == str(new_dest)


@pytest.mark.asyncio
async def test_execute_scrape_and_update_falls_back_to_product(env, monkeypatch):
    """源文件被删：用让位后的产物跑完，让位文件删掉，登记行的源路径保持原值。"""
    from server.models.scraper import ScrapeByIdRequest

    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    row = await _register(env, record.id)
    env["source_file"].unlink()
    new_dest = env["target_file"].parent / "示例剧集 - S01E09.mkv"
    scraper = _RecordingScraper(new_dest)
    _patch_scraper(monkeypatch, scraper)

    await _actions(env).execute_scrape_and_update(
        record.id,
        ScrapeByIdRequest(file_path=record.folder_path, tmdb_id=12345, season=1, episode=9),
        resolve_input=True,
    )

    request_path = Path(scraper.requests[0].file_path)
    assert request_path.name.startswith(env["target_file"].stem)  # 让位后的产物
    assert request_path.exists() is False  # 成功后删掉
    assert new_dest.exists() is True
    assert list(new_dest.parent.glob("*mhti-rescrape*")) == []

    updated = await env["history"].get_record(record.id)
    assert updated.folder_path == f"{env['target_file']} => {new_dest}"

    rows = await env["scraped"].list_by_history_record(record.id)
    assert len(rows) == 1
    assert rows[0].id == row.id
    assert rows[0].source_path == str(env["source_file"])  # 源路径仍是原来那个
    assert rows[0].target_path == str(new_dest)


@pytest.mark.asyncio
async def test_execute_scrape_and_update_reports_400_without_any_input(env, monkeypatch):
    """两侧都没了：400 且记录状态不变（不能把 success 记录改成 failed）。"""
    from server.models.scraper import ScrapeByIdRequest

    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    env["source_file"].unlink()
    env["target_file"].unlink()
    _patch_scraper(monkeypatch, _RecordingScraper(env["target_file"]))

    with pytest.raises(HTTPException) as exc:
        await _actions(env).execute_scrape_and_update(
            record.id,
            ScrapeByIdRequest(file_path=record.folder_path, tmdb_id=1, season=1, episode=1),
            resolve_input=True,
        )

    assert exc.value.status_code == 400
    after = await env["history"].get_record(record.id)
    assert after.status == TaskStatus.SUCCESS


@pytest.mark.asyncio
async def test_execute_scrape_and_update_restores_product_when_scrape_fails(env, monkeypatch):
    """刮削失败时让位文件必须放回原位，产物不能凭一次失败就消失。"""

    class _FailingScraper:
        async def scrape_by_id(self, request, on_log_update=None):
            raise RuntimeError("TMDB 连接失败")

    from server.models.scraper import ScrapeByIdRequest

    record = await _make_record(
        env, f"{env['source_file']} => {env['target_file']}"
    )
    env["source_file"].unlink()
    _patch_scraper(monkeypatch, _FailingScraper())

    with pytest.raises(RuntimeError):
        await _actions(env).execute_scrape_and_update(
            record.id,
            ScrapeByIdRequest(file_path=record.folder_path, tmdb_id=1, season=1, episode=1),
            resolve_input=True,
        )

    assert env["target_file"].exists() is True
    assert env["target_file"].read_bytes() == b"organized-bytes"
    assert list(env["target_file"].parent.glob("*mhti-rescrape*")) == []
    failed = await env["history"].get_record(record.id)
    assert failed.status == TaskStatus.FAILED
    assert failed.error_message == "TMDB 连接失败"


@pytest.mark.asyncio
async def test_stage_never_overwrites_previous_interrupted_staging_file(env):
    record = await _make_record(env, f"{env['source_file']} => {env['target_file']}")
    env['source_file'].unlink()
    stale = env['target_file'].with_name(f"{env['target_file'].stem}.mhti-rescrape{env['target_file'].suffix}")
    stale.write_bytes(b'previous recovery copy')
    resolver = _resolver(env)
    staged = await resolver.stage(await resolver.resolve(record))
    assert Path(staged.path) != stale
    assert stale.read_bytes() == b'previous recovery copy'
    await resolver.release(staged, dest_path=None, success=False)
    assert env['target_file'].read_bytes() == b'organized-bytes'
