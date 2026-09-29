"""媒体库扫描用例 - 扫描目录并过滤已刮削文件。

从 api/files.py 下沉，路由只做协议转换。
"""

from server.common.exceptions import validation_error
from server.models.file import ScannedFile
from server.models.storage import StorageLocator, StorageProvider
from server.domain.media.file_service import FileService
from server.domain.media.fingerprint_service import calculate_fingerprint
from server.application.history_service import HistoryService
from server.application.file_io import check_file_cancelled, run_file_io


def _fingerprints(files: list[ScannedFile]) -> dict[str, str]:
    """Calculate local file fingerprints in the bounded file-I/O executor."""
    result: dict[str, str] = {}
    for file in files:
        check_file_cancelled()
        fingerprint = calculate_fingerprint(file.path)
        if fingerprint:
            result[file.path] = fingerprint
    return result


class FolderScanUseCase:
    """扫描文件夹并按指纹过滤已刮削文件。"""

    def __init__(
        self,
        file_service: FileService,
        history_service: HistoryService,
    ) -> None:
        self._file_service = file_service
        self._history_service = history_service

    async def scan(
        self,
        folder_path: str,
        locator: StorageLocator | None,
        exclude_scraped: bool = True,
    ) -> tuple[str, list[ScannedFile], int]:
        """执行扫描。

        Returns:
            (显示用目录路径, 待刮削的文件列表, 被排除的已刮削文件数)
        """
        is_p115 = bool(locator and locator.provider == StorageProvider.P115)

        if not folder_path.strip() and not is_p115:
            raise validation_error("folder_path 不能为空", field="folder_path")

        if is_p115:
            files = await self._file_service.scan_folder_async(
                locator.path or folder_path,
                locator=locator,
            )
            # 115 文件没有本地指纹，直接返回
            return locator.path or folder_path, files, 0

        files = await run_file_io(self._file_service.scan_folder, folder_path)

        if not exclude_scraped:
            return folder_path, files, 0

        # 计算文件指纹并过滤已刮削的文件
        fingerprint_map = await run_file_io(_fingerprints, files)

        # 查询已存在的指纹
        existing_fps = await self._history_service.get_existing_fingerprints(
            list(fingerprint_map.values())
        )

        # 过滤掉已刮削的文件
        filtered_files = [
            f for f in files
            if fingerprint_map.get(f.path) not in existing_fps
        ]
        return folder_path, filtered_files, len(files) - len(filtered_files)
