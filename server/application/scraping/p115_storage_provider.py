"""115 网盘文件输出适配层。

从 scraper_service.py 拆出，供刮削编排复用。
"""

import asyncio
from pathlib import Path
from typing import Any

from server.models.storage import StorageLocator
from server.domain.system.config_service import ConfigService

DIRECTORY_PAGE_SIZE = 100


class P115StorageProvider:
    """115 网盘文件输出适配层。"""

    def __init__(self, config_service: ConfigService) -> None:
        self._config_service = config_service

    async def _get_client(self) -> tuple[Any, str]:
        from server.domain.integration import p115_service as p115_service_module

        config = await self._config_service.get_115_config()
        if not config.is_logged_in or not config.cookies.strip():
            raise ValueError("请先登录 115 网盘")

        p115_module, _ = await p115_service_module.load_p115client()
        # 参数必须与 P115Service._load_p115_client_with_config 一致：pinned 版本
        # （requirements.txt 的 0.0.9.6.5.1）不接受 check_for_relogin /
        # ensure_cookies，两个调用点任一漏改都会在登录后 500。
        client = p115_module.P115Client(
            config.cookies,
            app=config.app or p115_service_module.DEFAULT_APP,
            console_qrcode=False,
        )
        return client, config.app or p115_service_module.DEFAULT_APP

    def _extract_response_id(
        self,
        response: Any,
        *,
        fallback_id: str | None = None,
        fallback_parent_id: str | None = None,
    ) -> dict[str, str | None]:
        """从 115 接口响应中提取目录 ID。"""
        payload = response.get("data", response) if isinstance(response, dict) else response
        if not isinstance(payload, dict):
            return {"id": fallback_id, "parent_id": fallback_parent_id}

        target_id = payload.get("cid") or payload.get("id") or payload.get("file_id") or fallback_id
        parent_id = payload.get("pid") or payload.get("parent_id") or fallback_parent_id
        return {
            "id": None if target_id in (None, "") else str(target_id),
            "parent_id": None if parent_id in (None, "") else str(parent_id),
        }

    @staticmethod
    def _ensure_operation_succeeded(response: Any, operation: str) -> None:
        """Treat an explicit ``state: false`` response as an operation failure."""
        if isinstance(response, dict) and response.get("state") is False:
            message = response.get("message") or response.get("msg") or "未知原因"
            raise ValueError(f"115 {operation}失败: {message}")

    async def ensure_directory(
        self,
        locator: StorageLocator,
        relative_path: str,
    ) -> dict[str, str | None]:
        """确保目标目录存在。

        用 ``fs_mkdir`` 逐层创建（不依赖 ``batch_makedir``，后者在 async 模式下
        存在协程未 await 的 bug，导致目录不会被实际创建）。

        注意：``fs_mkdir`` 对已存在目录返回 ``state:False`` 且不含 id，必须
        用 ``fs_files`` 查询该层目录的真实 id，否则后续文件会被移到错误目录。
        """
        base_id = locator.file_id or locator.parent_id
        if not base_id:
            raise ValueError("115 输出目录缺少 file_id")

        if not relative_path or relative_path == ".":
            return {"id": str(base_id), "parent_id": locator.parent_id}

        client, _ = await self._get_client()

        # 拆分相对路径，逐层创建
        parts = [p for p in relative_path.replace("\\", "/").split("/") if p and p != "."]
        current_pid = str(base_id)
        for name in parts:
            response = await client.fs_mkdir(name, pid=current_pid, async_=True)
            # fs_mkdir 成功时返回新目录 id
            new_id = None
            if isinstance(response, dict):
                if response.get("state"):
                    new_id = response.get("cid") or response.get("file_id") or response.get("id")
                # state:False 表示目录已存在 → 查询真实 id
            if not new_id:
                new_id = await self._find_subdir_id(client, current_pid, name)
            if not new_id:
                raise ValueError(f"115 创建目录失败或无法定位: {name}")
            current_pid = str(new_id)

        return {"id": current_pid, "parent_id": locator.parent_id}

    async def _find_subdir_id(
        self,
        client: Any,
        parent_pid: str,
        name: str,
    ) -> str | None:
        """在父目录下按名字查找子目录的 cid（fs_mkdir 命中已存在目录时用）。"""
        for row in await self._list_directory_rows(client, parent_pid):
            if not isinstance(row, dict):
                continue
            row_name = row.get("n") or row.get("name") or ""
            # 目录条目用 cid，文件用 fid
            cid = row.get("cid") or row.get("id")
            if row_name == name and cid and "fid" not in row:
                return str(cid)
        return None

    async def rename(
        self,
        locator: StorageLocator,
        target_name: str,
        target_parent_id: str,
    ) -> dict[str, Any]:
        """移动并重命名文件。

        用 ``fs_rename`` + ``fs_move`` 组合实现，绕开 ``renamefile`` 内部的
        ``download_url`` 反查（某些文件反查会报 "index out of bounds"）。
        """
        if not locator.file_id:
            raise ValueError("115 源文件缺少 file_id")

        client, _ = await self._get_client()
        file_id = locator.file_id
        original_parent_id = locator.parent_id
        original_name = Path(locator.path).name

        # 1. 先移动，再改名。这样改名失败时可以把文件可靠地移回原目录。
        try:
            move_response = await client.fs_move(file_id, pid=target_parent_id, async_=True)
            self._ensure_operation_succeeded(move_response, "文件移动")
        except Exception as exc:
            raise ValueError(f"115 文件移动失败: {file_id} -> {target_parent_id}") from exc

        if target_name and target_name != original_name:
            try:
                rename_response = await client.fs_rename((file_id, target_name), async_=True)
                self._ensure_operation_succeeded(rename_response, "文件改名")
            except Exception as exc:
                if original_parent_id:
                    try:
                        rollback = await client.fs_move(
                            file_id,
                            pid=original_parent_id,
                            async_=True,
                        )
                        self._ensure_operation_succeeded(rollback, "文件回滚")
                    except Exception as rollback_exc:
                        raise ValueError(
                            f"115 文件改名失败且无法移回原目录: {target_name}"
                        ) from rollback_exc
                    raise ValueError(
                        f"115 文件改名失败，已移回原目录: {target_name}"
                    ) from exc
                raise ValueError(f"115 文件改名失败: {target_name}") from exc

        return move_response

    async def copy(
        self,
        locator: StorageLocator,
        target_name: str,
        target_parent_id: str,
    ) -> dict[str, Any]:
        """复制文件到目标目录并改名。

        用 ``fs_copy`` + ``fs_rename`` 组合实现，绕开 ``copyfile`` 内部的
        ``download_url`` 反查。

        注意：``fs_copy`` 响应不含新文件 id，必须复制后在目标目录按源文件名
        查找新文件的 id，才能执行改名。
        """
        if not locator.file_id:
            raise ValueError("115 源文件缺少 file_id")

        client, _ = await self._get_client()
        file_id = locator.file_id
        source_name = Path(locator.path).name

        # 1. 记录目标目录已有文件，复制到目标目录（保持原名）
        before_ids = await self._find_file_ids_in_dir(client, target_parent_id, source_name)
        try:
            resp = await client.fs_copy(file_id, pid=target_parent_id, async_=True)
            self._ensure_operation_succeeded(resp, "文件复制")
        except Exception as exc:
            raise ValueError(f"115 文件复制失败: {file_id} -> {target_parent_id}") from exc

        # 2. fs_copy 通常不返回新文件 id；用复制前后的 fid 差集定位新副本，
        # 避免把同名旧文件误改名。
        new_id = self._extract_response_id(resp).get("id")
        if not new_id or new_id == file_id:
            for attempt in range(3):
                try:
                    after_ids = await self._find_file_ids_in_dir(
                        client, target_parent_id, source_name
                    )
                except ValueError as exc:
                    if attempt == 2:
                        raise ValueError(
                            "115 复制后无法读取目标目录，复制状态未知；请先确认网盘结果再重试"
                        ) from exc
                    await asyncio.sleep(0.05)
                    continue

                candidates = after_ids - before_ids
                if len(candidates) == 1:
                    new_id = candidates.pop()
                    break
                if attempt < 2:
                    await asyncio.sleep(0.05)
        if not new_id:
            raise ValueError("115 复制成功但无法定位新文件")

        # 3. 改名为目标名
        if target_name and target_name != source_name:
            try:
                rename_response = await client.fs_rename((new_id, target_name), async_=True)
                self._ensure_operation_succeeded(rename_response, "复制文件改名")
            except Exception as exc:
                raise ValueError(f"115 复制后改名失败: {target_name}") from exc

        if isinstance(resp, dict):
            return {**resp, "file_id": str(new_id)}
        return {"response": resp, "file_id": str(new_id)}

    async def _find_file_ids_in_dir(
        self,
        client: Any,
        parent_pid: str,
        name: str,
    ) -> set[str]:
        """Return all matching file ids in a directory."""
        return {
            str(row.get("fid"))
            for row in await self._list_directory_rows(client, parent_pid)
            if isinstance(row, dict)
            and (row.get("n") or row.get("name") or "") == name
            and row.get("fid")
        }

    async def _list_directory_rows(
        self,
        client: Any,
        parent_pid: str,
    ) -> list[dict[str, Any]]:
        """Read every page of a 115 directory listing."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            try:
                response = await client.fs_files(
                    {
                        "cid": parent_pid,
                        "offset": offset,
                        "limit": DIRECTORY_PAGE_SIZE,
                        "show_dir": 1,
                    },
                    async_=True,
                )
            except Exception as exc:
                # An unreadable listing is not an empty directory.  Returning
                # [] could make copy/rename continue with an incomplete
                # snapshot and create duplicate files on a later retry.
                raise ValueError(f"115 目录读取失败: {parent_pid}") from exc

            if not isinstance(response, dict) or not response.get("state", True):
                if isinstance(response, dict):
                    message = (
                        response.get("message")
                        or response.get("error")
                        or response.get("msg")
                        or "未知原因"
                    )
                else:
                    message = "返回格式无效"
                raise ValueError(f"115 目录读取失败: {parent_pid} ({message})")

            page = response.get("data", [])
            if not isinstance(page, list) or not page:
                break
            rows.extend(row for row in page if isinstance(row, dict))
            if len(page) < DIRECTORY_PAGE_SIZE:
                break
            offset += DIRECTORY_PAGE_SIZE
        return rows

    async def _find_file_id_in_dir(
        self,
        client: Any,
        parent_pid: str,
        name: str,
    ) -> str | None:
        """在父目录下按名字查找文件的 fid（fs_copy 后定位新副本用）。"""
        ids = await self._find_file_ids_in_dir(client, parent_pid, name)
        return next(iter(ids), None)

    async def download(self, locator: StorageLocator, destination_dir: Path) -> Path:
        """下载 115 文件到本地临时目录。"""
        if not locator.file_id:
            raise ValueError("115 源文件缺少 file_id")

        from p115client.tool import download as download_tool

        filename = Path(locator.path).name or locator.file_id
        local_path = destination_dir / filename
        client, _ = await self._get_client()
        task_result = await download_tool.download_file(
            client,
            locator.file_id,
            path=str(local_path),
            async_=True,
        )
        if not task_result and task_result.error is not None:
            raise task_result.error
        return local_path
