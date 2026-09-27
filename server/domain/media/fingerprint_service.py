"""文件指纹服务 - 计算文件内容哈希用于去重."""

import hashlib
from pathlib import Path

from server.common.path_security import PathSecurityError, validate_media_path

# 每个采样块的大小
SAMPLE_SIZE = 4096  # 4KB


def calculate_fingerprint(file_path: str | Path) -> str | None:
    """
    计算文件指纹（部分哈希）.

    使用文件的前4KB + 中间4KB + 后4KB + 文件大小计算哈希，
    这样可以快速处理大文件，同时保持较高的准确性。

    Args:
        file_path: 文件路径

    Returns:
        文件指纹字符串，格式为 "size_hash"，如果文件不存在返回 None
    """
    try:
        path = validate_media_path(
            str(file_path),
            must_exist=True,
            require_file=True,
        )
    except PathSecurityError:
        return None

    try:
        file_size = path.stat().st_size
        hasher = hashlib.md5()

        with open(path, "rb") as f:
            # 读取前 4KB
            hasher.update(f.read(SAMPLE_SIZE))

            if file_size > SAMPLE_SIZE * 2:
                # 读取中间 4KB
                mid_pos = (file_size - SAMPLE_SIZE) // 2
                f.seek(mid_pos)
                hasher.update(f.read(SAMPLE_SIZE))

                # 读取后 4KB
                f.seek(-SAMPLE_SIZE, 2)
                hasher.update(f.read(SAMPLE_SIZE))

        # 将文件大小也加入哈希计算
        hasher.update(str(file_size).encode())

        return f"{file_size}_{hasher.hexdigest()[:16]}"
    except (OSError, IOError):
        return None


def calculate_fingerprints(file_paths: list[str]) -> dict[str, str]:
    """
    批量计算文件指纹.

    Args:
        file_paths: 文件路径列表

    Returns:
        字典，key 为文件路径，value 为指纹
    """
    result = {}
    for path in file_paths:
        fp = calculate_fingerprint(path)
        if fp:
            result[path] = fp
    return result
