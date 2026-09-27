"""重新刮削 / 重新整理的输入文件解析与旧产物清理。

为什么单独一层：记录里的 `folder_path` 在整理成功后是 `源文件 => 产物` 一整串
（见 history_service.build_record_folder_path），拿去当路径必然报「文件不存在」。
重刮真正要跑的是**源文件**：源文件没了才退回整理后的产物——这一点要被重刮与
重新整理共用，否则两处会各自漂移。

顺序（`resolve`）：
1. 已登记且存在于磁盘的源文件；
2. 记录 folder_path 里的源文件一侧；
3. 已登记的产物；
4. 记录 folder_path 里的产物一侧。

产物被当作输入时**先让位**（同目录改名成 `*.mhti-rescrape<ext>`）再跑，否则
「输入 == 目标路径」既会让整理步骤报「目标文件已存在」，也会让 copy 模式复制到
自己身上（shutil 抛 SameFileError）。跑成功就把让位文件删掉（它已是上一轮的旧产物），
跑失败则改名回原位，磁盘上不留意外文件。

旧产物清理复用 `HistoryFileService` 的清单（视频 + 元数据），因此只会删登记过、
且在允许目录内的文件；登记行**不删**——重刮收尾会把同一行更新成新路径。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from fastapi import HTTPException

from server.common.path_security import PathSecurityError, validate_media_path
from server.models.storage import is_p115_virtual_path

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
                    return RescrapeInput(path=path, from_product=from_product)
                continue
            try:
                safe_path = validate_media_path(
                    path,
                    must_exist=True,
                    require_file=True,
                )
            except PathSecurityError:
                continue
            return RescrapeInput(path=str(safe_path), from_product=from_product)

        raise HTTPException(status_code=400, detail="源文件与产物都不存在，无法重刮")

    async def _candidates(self, record) -> list[tuple[str, bool]]:
        """按「源文件优先、产物兜底」列出候选路径（不去重，由调用方取第一个可用者）。"""
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

    # ---- 让位 ----

    async def stage(self, input_: RescrapeInput) -> RescrapeInput:
        """产物作为输入时先改名让出原路径，返回更新后的 RescrapeInput。"""
        if not input_.from_product:
            return input_

        if _is_remote_path(input_.path):
            return input_

        try:
            original = validate_media_path(
                input_.path,
                must_exist=True,
                require_file=True,
            )
        except PathSecurityError:
            return input_

        staged = original.with_name(f"{original.stem}{_STAGE_SUFFIX}{original.suffix}")
        try:
            if staged.exists():  # 上次异常退出留下的同名临时文件
                safe_staged = validate_media_path(
                    str(staged),
                    must_exist=True,
                    require_file=True,
                )
                safe_staged.unlink()
            original.rename(staged)
        except (OSError, PathSecurityError) as exc:
            # 让位失败不阻断重刮：最坏情况是整理步骤报「目标文件已存在」，
            # 用户能在错误信息里看到原因，文件本身没被改动
            logger.warning("重刮输入让位失败 %s: %s", original, exc)
            return input_

        logger.info("重刮输入让位: %s -> %s", original, staged)
        return replace(input_, path=str(staged), staged_from=str(original))

    async def release(self, input_: RescrapeInput, *, dest_path: str | None, success: bool) -> None:
        """让位文件收尾：失败改名回原位，成功后删除。"""
        if not input_.staged_from:
            return

        if _is_remote_path(input_.path) or _is_remote_path(input_.staged_from):
            return

        try:
            staged = validate_media_path(input_.path)
            original = validate_media_path(input_.staged_from)
        except PathSecurityError as exc:
            logger.warning("重刮让位路径不在允许目录内: %s", exc)
            return

        if not success:
            if staged.exists() and not original.exists():
                try:
                    safe_staged = validate_media_path(
                        str(staged),
                        must_exist=True,
                        require_file=True,
                    )
                    safe_staged.rename(original)
                    logger.info("重刮失败，产物已放回原位: %s", original)
                except (OSError, PathSecurityError) as exc:
                    logger.warning("重刮失败且产物放回原位失败 %s: %s", original, exc)
            return

        if not staged.exists():
            # MOVE 模式下新产物就是拿让位文件动的，原路径已经空了
            return

        if dest_path and Path(dest_path).is_symlink():
            try:
                validate_media_path(dest_path, must_exist=True)
                if Path(dest_path).resolve() == staged.resolve():
                    # 软链接产物指向让位文件，删了就等于把产物删了
                    logger.warning("产物是软链接，保留让位文件 %s", staged)
                    return
            except (OSError, PathSecurityError) as exc:
                logger.warning("产物软链接路径校验失败，保留让位文件: %s", exc)
                return

        try:
            safe_staged = validate_media_path(
                str(staged),
                must_exist=True,
                require_file=True,
            )
            safe_staged.unlink()
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
