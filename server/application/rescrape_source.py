"""重刮与重新整理共用的输入校验、旧产物暂存及恢复。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from fastapi import HTTPException

from server.common.path_security import PathSecurityError, validate_media_file_entry, validate_output_entry, validate_media_path
from server.models.storage import is_p115_virtual_path
from server.infrastructure.file_journal import operation_kind, signature
from server.infrastructure.file_operations import path_operation_lock

logger = logging.getLogger(__name__)

# 产物让位时的文件名后缀（与产物同目录，避免跨盘复制大文件）
_STAGE_SUFFIX = ".mhti-rescrape"


def _is_remote_path(path: str) -> bool:
    """Recognize provider namespaces and legacy remote URI paths."""
    return is_p115_virtual_path(path) or "://" in path


@dataclass
class RescrapeInput:
    """本次实际喂给刮削流程的输入文件。"""

    path: str
    """刮削请求里用的 file_path。"""

    from_product: bool
    """True 表示源文件已不可用，本次输入取的是整理后的产物。"""

    staged_from: str | None = None
    """产物让位前的原始路径；None 表示没有让位动作（源文件输入 / 云端 / 让位失败）。"""
    history_record_id: str | None = None
    expected_signature: dict | None = None
    prior_product: RescrapeInput | None = None
    episode_files: dict[Path, dict] = field(default_factory=dict)


class RescrapeSource:
    """为一条历史记录准备重跑输入，并清理它上一轮的产物。"""

    def __init__(self, scraped_file_service, file_service=None) -> None:
        self._scraped_file_service = scraped_file_service
        # 文件清单/清理服务；缺省时只做输入解析，不清理旧产物（单元测试常用）
        self._file_service = file_service

    # ---- 解析 ----

    async def resolve(self, record, *, remote: bool = False) -> RescrapeInput:
        """挑出本次要跑的输入文件；源文件与产物都不存在时报 400。"""
        candidates = await self._candidates(record)
        for path, from_product in candidates:
            if _is_remote_path(path):
                if remote:
                    return RescrapeInput(path=path, from_product=from_product, history_record_id=record.id)
                continue
            try:
                safe_path = validate_media_path(
                    path,
                    must_exist=True,
                    require_file=True,
                )
            except PathSecurityError:
                continue
            # A link can retain its inode while its referent is replaced.
            if Path(path).is_symlink():
                continue
            observed = signature(safe_path)
            if not observed:
                continue
            if from_product:
                owned = await self._scraped_file_service.can_delete_target(record.id, path)
            else:
                owned = await self._scraped_file_service.can_delete_source(record.id, path)
                if (not owned and len(candidates) == 1 and getattr(record, "file_fingerprint", None)
                        and not await self._scraped_file_service.is_scraped(path)):
                    from server.application.file_io import run_file_io
                    from server.domain.media.fingerprint_service import calculate_fingerprint
                    owned = await run_file_io(calculate_fingerprint, path) == record.file_fingerprint
                if owned:
                    owned = await self._scraped_file_service.can_delete_target(
                        record.id, path, allow_unregistered=True,
                    )
            if not owned or signature(safe_path) != observed:
                continue
            return RescrapeInput(path=str(safe_path), from_product=from_product,
                                 history_record_id=record.id, expected_signature=observed)

        raise HTTPException(status_code=400, detail="源文件与产物都不存在或无法确认归属，无法重刮")

    async def _candidates(self, record) -> list[tuple[str, bool]]:
        """按「源文件优先、产物兜底」列出候选路径（按路径去重）。"""
        from server.application.history_service import split_record_folder_path

        registered = await self._scraped_file_service.list_by_history_record(record.id)
        folder_source, folder_target = split_record_folder_path(record.folder_path)

        ordered: list[tuple[str | None, bool]] = [
            *((item.source_path, False) for item in registered),
            (folder_source, False),
            *((item.target_path, True) for item in registered),
            (folder_target, True),
        ]

        seen: set[str] = set()
        result: list[tuple[str, bool]] = []
        for raw, from_product in ordered:
            path = (raw or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            result.append((path, from_product))
        return result

    async def prepare(self, record, *, remote: bool) -> RescrapeInput:
        selected = await self.resolve(record, remote=remote)
        if _is_remote_path(selected.path):
            return selected
        if selected.from_product:
            return await self._stage_prior(selected)
        for entry in await self._scraped_file_service.list_by_history_record(record.id):
            path = entry.target_path
            if not path or path == selected.path or _is_remote_path(path):
                continue
            if not await self._scraped_file_service.can_delete_target(record.id, path):
                continue
            previous = RescrapeInput(path=path, from_product=True, history_record_id=record.id,
                                     expected_signature=signature(Path(path)))
            selected.prior_product = await self._stage_prior(previous)
            break
        return selected

    async def _stage_prior(self, previous: RescrapeInput) -> RescrapeInput:
        if self._file_service is not None:
            from server.models.history import HistoryFileRole
            listing = await self._file_service.list_files(previous.history_record_id)
            stem = Path(previous.path).stem
            for entry in listing.files:
                path = Path(entry.path)
                if (entry.deletable and entry.role == HistoryFileRole.METADATA
                        and path.stem in {stem, stem + "-thumb"}):
                    value = signature(path)
                    if value is not None:
                        previous.episode_files[path] = value
        return await self.stage(previous)

    # ---- 让位 ----

    async def stage(self, input_: RescrapeInput) -> RescrapeInput:
        """产物作为输入时先改名让出原路径，返回更新后的 RescrapeInput。"""
        if not input_.from_product:
            return input_

        if _is_remote_path(input_.path):
            return input_

        try:
            original = validate_media_file_entry(input_.path)
        except PathSecurityError:
            return input_

        staged = original.with_name(
            f"{original.stem}{_STAGE_SUFFIX}-{uuid.uuid4().hex}{original.suffix}"
        )
        if input_.history_record_id is not None and not await self._scraped_file_service.can_delete_target(input_.history_record_id, input_.path):
            raise HTTPException(status_code=409, detail="该产物已由其他记录替换，无法作为旧记录输入")
        try:
            from server.infrastructure.file_operations import publish_file
            from server.models.organize import OrganizeMode

            with path_operation_lock(original), operation_kind("stage"):
                if input_.expected_signature is not None and signature(original) != input_.expected_signature:
                    raise HTTPException(status_code=409, detail="重刮输入已被替换，请重新加载记录")
                publish_file(original, staged, OrganizeMode.MOVE)
        except (OSError, PathSecurityError) as exc:
            # 让位失败不阻断重刮：最坏情况是整理步骤报「目标文件已存在」，
            # 用户能在错误信息里看到原因，文件本身没被改动
            logger.warning("重刮输入让位失败 %s: %s", original, exc)
            return input_

        logger.info("重刮输入让位: %s -> %s", original, staged)
        return replace(input_, path=str(staged), staged_from=str(original), expected_signature=signature(staged))

    async def release(self, input_: RescrapeInput, *, dest_path: str | None, success: bool) -> None:
        """让位文件收尾：失败改名回原位，成功后删除。"""
        if input_.prior_product is not None:
            await self.release(input_.prior_product, dest_path=dest_path, success=success)
        if not input_.staged_from:
            return

        if _is_remote_path(input_.path) or _is_remote_path(input_.staged_from):
            return

        try:
            staged = validate_output_entry(Path(input_.path))
            original = validate_output_entry(Path(input_.staged_from))
        except PathSecurityError as exc:
            logger.warning("重刮让位路径不在允许目录内: %s", exc)
            return

        # Remove only unchanged episode-specific sidecars after a successful rename.
        if success and dest_path and Path(dest_path).stem != original.stem and not original.exists():
            from server.domain.media.file_service import SUPPORTED_VIDEO_EXTENSIONS
            occupied = any(p.stem == original.stem and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
                           for p in original.parent.iterdir())
            if not occupied:
                for sidecar, expected in input_.episode_files.items():
                    with path_operation_lock(sidecar):
                        if signature(sidecar) == expected:
                            validate_media_file_entry(str(sidecar)).unlink()
        if signature(staged) is None:
            return
        if signature(staged) != input_.expected_signature:
            logger.warning("让位文件已变化，保留文件 %s", staged)
            return
        if not success:
            if not original.exists() and not original.is_symlink():
                from server.infrastructure.file_operations import restore_file_entry
                restore_file_entry(staged, original)
            return

        if dest_path and Path(dest_path).is_symlink():
            try:
                validate_media_path(dest_path, must_exist=True)
                if not staged.is_symlink() and Path(dest_path).resolve() == staged:
                    # 软链接产物指向让位文件，删了就等于把产物删了
                    logger.warning("产物是软链接，保留让位文件 %s", staged)
                    return
            except (OSError, PathSecurityError) as exc:
                logger.warning("产物软链接路径校验失败，保留让位文件: %s", exc)
                return

        try:
            staged.unlink()
            logger.info("已删除上一轮产物副本: %s", staged)
        except (OSError, PathSecurityError) as exc:
            logger.warning("删除让位文件失败 %s: %s", staged, exc)

    # ---- 清理 ----

    async def clear_products(
        self, record_id: str, *, keep_paths: set[str] | None = None
    ) -> list[str]:
        """删掉这一轮的旧产物（视频 + 元数据），返回被删除的路径。"""
        if self._file_service is None:
            return []
        response = await self._file_service.clear_products(
            record_id, keep_paths=keep_paths or set()
        )
        return [item.path for item in response.results if item.deleted]
