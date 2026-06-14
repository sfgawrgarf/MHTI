"""115 login service."""

import asyncio
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from server.core.exceptions import ConfigurationError, FolderNotFoundError, InvalidFolderError
from server.models.cloud_115 import (
    Cloud115Config,
    Cloud115DeviceOption,
    Cloud115QrSession,
    Cloud115QrStatus,
    Cloud115Status,
)
from server.services.config_service import ConfigService

DEFAULT_APP = "alipaymini"
PROJECT_P115_HOME = Path(__file__).resolve().parents[2] / "data" / "p115-home"
QRCODE_PAYLOAD_PREFIX = "cloud_115_qr_payload:"
VIRTUAL_115_ROOT_PATH = "/115网盘"
# Mirrors file_service.SUPPORTED_VIDEO_EXTENSIONS (kept local to avoid a circular import).
SCAN_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".rmvb", ".ts",
    ".m2ts", ".bdmv", ".webm", ".3gp", ".mpg", ".mpeg", ".vob", ".iso",
}
STANDARD_DEVICE_LABELS = {
    "web": "115生活_网页端",
    "ios": "115生活_苹果端",
    "115ios": "115_苹果端",
    "android": "115生活_安卓端",
    "115android": "115_安卓端",
    "ipad": "115生活_苹果平板端",
    "115ipad": "115_苹果平板端",
    "tv": "115生活_安卓电视端",
    "apple_tv": "115生活_苹果电视端",
    "qandroid": "115管理_安卓端",
    "qios": "115管理_苹果端",
    "qipad": "115管理_苹果平板端",
    "os_windows": "115生活_Windows端",
    "os_mac": "115生活_macOS端",
    "os_linux": "115生活_Linux端",
    "wechatmini": "115生活_微信小程序端",
    "alipaymini": "115生活_支付宝小程序",
    "harmony": "115_鸿蒙端",
}
ALIAS_DEVICE_LABELS = {
    "desktop": "115浏览器",
    "bios": "未知: ios",
    "bandroid": "未知: android",
    "bipad": "未知: ipad",
    "windows": "Windows 别名",
    "mac": "macOS 别名",
    "linux": "Linux 别名",
}
STATUS_MESSAGES = {
    0: ("pending", "等待扫码"),
    1: ("scanned", "已扫码，等待确认"),
    2: ("success", "登录成功"),
    -1: ("expired", "二维码已过期"),
    -2: ("canceled", "二维码已取消"),
}
_P115_IMPORT_LOCK = asyncio.Lock()
_P115_IMPORT_SYNC_LOCK = threading.Lock()
_P115_MODULE_CACHE: tuple[Any, Any] | None = None


@contextmanager
def _temporary_p115_home():
    """Temporarily point HOME/USERPROFILE to a writable project path."""
    PROJECT_P115_HOME.mkdir(parents=True, exist_ok=True)
    previous_home = os.environ.get("HOME")
    previous_userprofile = os.environ.get("USERPROFILE")
    writable_home = str(PROJECT_P115_HOME)
    os.environ["HOME"] = writable_home
    os.environ["USERPROFILE"] = writable_home
    try:
        yield
    finally:
        if previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous_home
        if previous_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = previous_userprofile


def _load_p115client_sync() -> tuple[Any, Any]:
    """Synchronously import p115client once with temporary writable HOME settings."""
    global _P115_MODULE_CACHE
    if _P115_MODULE_CACHE is not None:
        return _P115_MODULE_CACHE

    with _P115_IMPORT_SYNC_LOCK:
        if _P115_MODULE_CACHE is not None:
            return _P115_MODULE_CACHE

        import importlib

        with _temporary_p115_home():
            p115_module = importlib.import_module("p115client")
            const_module = importlib.import_module("p115client.const")
        _P115_MODULE_CACHE = (p115_module, const_module)
        return _P115_MODULE_CACHE


async def _load_p115client() -> tuple[Any, Any]:
    """Safely import p115client once with temporary writable HOME settings."""
    if _P115_MODULE_CACHE is not None:
        return _P115_MODULE_CACHE

    async with _P115_IMPORT_LOCK:
        return _load_p115client_sync()


class P115Service:
    """Service for managing 115 QR login and status."""

    def __init__(self, config_service: ConfigService):
        self.config_service = config_service

    def list_login_devices(self) -> list[Cloud115DeviceOption]:
        """Return supported standard apps and recognized aliases."""
        _, const_module = _load_p115client_sync()

        items: list[Cloud115DeviceOption] = []
        standard_values = list(dict.fromkeys(const_module.AVAILABLE_APPS.keys()))
        alias_values = [
            value
            for value in dict.fromkeys(const_module.APP_TO_SSOENT.keys())
            if value not in const_module.AVAILABLE_APPS
        ]

        for value in standard_values:
            items.append(
                Cloud115DeviceOption(
                    value=value,
                    label=STANDARD_DEVICE_LABELS.get(
                        value,
                        const_module.AVAILABLE_APPS.get(value, value),
                    ),
                    group="standard",
                )
            )

        for value in alias_values:
            mapped = self._normalize_app(value)
            items.append(
                Cloud115DeviceOption(
                    value=value,
                    label=ALIAS_DEVICE_LABELS.get(
                        value,
                        STANDARD_DEVICE_LABELS.get(mapped, mapped),
                    ),
                    group="alias",
                )
            )

        return items

    async def get_status(self) -> Cloud115Status:
        """Return persisted 115 login status."""
        config = await self.config_service.get_115_config()
        return Cloud115Status(
            enabled=config.enabled,
            app=config.app or DEFAULT_APP,
            is_logged_in=config.is_logged_in,
            updated_at=config.updated_at,
        )

    async def clear_login_state(self) -> None:
        """Clear persisted 115 login config and any pending QR sessions."""
        await self.config_service.delete_115_config()
        await self._delete_all_qr_payloads()

    async def start_qr_login(self, app: str) -> Cloud115QrSession:
        """Start a QR login session."""
        normalized_app = self._normalize_app(app)
        p115_module, _ = await _load_p115client()
        token_response = await p115_module.P115Client.login_qrcode_token(
            app=normalized_app,
            async_=True,
        )
        token_data = token_response["data"]
        uid = token_data["uid"]
        qrcode_url = token_data.get("qrcode") or f"https://115.com/scan/dg-{uid}"
        await self._save_qr_payload(uid, token_data, normalized_app)
        return Cloud115QrSession(uid=uid, qrcode_url=qrcode_url, app=normalized_app)

    async def poll_qr_login(self, uid: str, app: str) -> Cloud115QrStatus:
        """Poll QR login status and persist cookies after success."""
        token_payload = await self._get_qr_payload(uid)
        normalized_app = self._resolve_qr_app(token_payload, app)
        if not self._has_complete_qr_payload(token_payload):
            await self._delete_qr_payload(uid)
            return Cloud115QrStatus(
                uid=uid,
                app=normalized_app,
                status="expired",
                message="二维码登录会话不存在或已过期，请重新扫码",
                is_logged_in=False,
            )
        p115_module, _ = await _load_p115client()

        status_response = await p115_module.P115Client.login_qrcode_scan_status(
            self._build_scan_status_payload(uid, token_payload),
            async_=True,
        )
        raw_status = status_response.get("data", {}).get("status")
        status, message = STATUS_MESSAGES.get(raw_status, ("unknown", "未知登录状态"))

        if raw_status != 2:
            if raw_status in {-1, -2}:
                await self._delete_qr_payload(uid)
            return Cloud115QrStatus(
                uid=uid,
                app=normalized_app,
                status=status,
                message=message,
                is_logged_in=False,
            )

        result_response = await p115_module.P115Client.login_qrcode_scan_result(
            uid,
            app=normalized_app,
            async_=True,
        )
        cookies = self._extract_cookies(result_response)
        if not cookies:
            return Cloud115QrStatus(
                uid=uid,
                app=normalized_app,
                status="scanned",
                message="已扫码确认，但未获取到登录 cookies",
                is_logged_in=False,
            )

        await self.config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app=normalized_app,
                cookies=cookies,
                is_logged_in=bool(cookies),
                updated_at=datetime.now(),
            )
        )
        await self._delete_qr_payload(uid)

        return Cloud115QrStatus(
            uid=uid,
            app=normalized_app,
            status="success",
            message=message,
            is_logged_in=True,
        )

    async def browse(
        self,
        *,
        path: str = VIRTUAL_115_ROOT_PATH,
        file_id: str | None = "0",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Browse 115 directory contents with an async-only API."""
        return await self._browse_async(
            path=path,
            file_id=file_id,
            page=page,
            page_size=page_size,
        )

    async def scan_folder(
        self,
        *,
        path: str = VIRTUAL_115_ROOT_PATH,
        file_id: str | None = "0",
    ) -> list[dict[str, Any]]:
        """Recursively scan a 115 directory for video files.

        Unlike :meth:`browse`, this walks every sub-directory and returns a flat
        list of video-file entries (no pagination). Each entry keeps the same
        shape produced by :meth:`browse` (name/path/is_dir/provider/file_id/
        parent_id/size/mtime) so callers can rebuild a :class:`StorageLocator`.
        """
        config = await self.config_service.get_115_config()
        if not config.is_logged_in or not config.cookies.strip():
            raise ConfigurationError("请先登录 115 网盘", config_key="cloud_115_config")

        normalized_path = self._normalize_virtual_path(path)
        client = await self._load_p115_client_with_config(config)
        root_directory_id = await self._resolve_directory_id(
            client=client,
            path=normalized_path,
            file_id=file_id,
        )
        collected: list[dict[str, Any]] = []
        await self._scan_recursive(
            client=client,
            directory_id=root_directory_id,
            current_path=normalized_path,
            collected=collected,
        )
        return collected

    async def _scan_recursive(
        self,
        *,
        client: Any,
        directory_id: str,
        current_path: str,
        collected: list[dict[str, Any]],
    ) -> None:
        """Depth-first scan collecting video files and recursing into folders."""
        page_size = 100
        offset = 0
        while True:
            response = await self._call_browse_api(
                client.fs_files,
                {
                    "cid": directory_id,
                    "offset": offset,
                    "limit": page_size,
                    "show_dir": 1,
                },
                async_=True,
                error_path=current_path,
            )
            rows = response.get("data", []) or []
            if not rows:
                break

            current_path_resolved, _parent_path, _current_fid, _parent_fid = await self._call_browse_api(
                self._resolve_browse_paths,
                client=client,
                response=response,
                directory_id=directory_id,
                requested_path=current_path,
                error_path=current_path,
            )

            for row in rows:
                if not isinstance(row, dict):
                    continue
                entry = self._normalize_browse_entry(row, current_path_resolved)
                if entry["is_dir"]:
                    child_id = entry.get("file_id") or "0"
                    await self._scan_recursive(
                        client=client,
                        directory_id=child_id,
                        current_path=entry["path"],
                        collected=collected,
                    )
                else:
                    if self._is_video_filename(entry.get("name") or ""):
                        collected.append(entry)

            # Stop when the page is not full (last page) or total is exhausted.
            if len(rows) < page_size:
                break
            offset += page_size

    @staticmethod
    def _is_video_filename(name: str) -> bool:
        """Return True if the filename looks like a supported video file."""
        dot = name.rfind(".")
        if dot < 0:
            return False
        return name[dot:].lower() in SCAN_VIDEO_EXTENSIONS

    async def _load_p115_client_with_config(self, config: Cloud115Config) -> Any:
        """Build a configured P115Client from persisted login config."""
        p115_module, _ = await _load_p115client()
        return p115_module.P115Client(
            config.cookies,
            check_for_relogin=False,
            ensure_cookies=False,
            app=self._normalize_app(config.app),
            console_qrcode=False,
        )

    def _build_login_expired_error(self) -> ConfigurationError:
        """Return a user-facing error for expired 115 login state."""
        return ConfigurationError(
            "115 登录已失效，请重新扫码登录",
            config_key="cloud_115_config",
        )

    def _build_generic_browse_error(self) -> ConfigurationError:
        """Return a generic provider browse failure error."""
        return ConfigurationError(
            "115 网盘目录浏览失败，请稍后重试",
            config_key="cloud_115_config",
        )

    def _map_browse_error(self, exc: Exception, path: str) -> Exception:
        """Translate provider exceptions into project-level browse semantics."""
        if isinstance(exc, (ConfigurationError, FolderNotFoundError, InvalidFolderError)):
            return exc

        exc_name = type(exc).__name__
        message = str(exc)
        normalized_message = message.lower()

        if exc_name in {
            "P115AuthenticationError",
            "P115LoginError",
            "P115AccessTokenError",
            "P115OpenAppAuthLimitExceeded",
        }:
            return self._build_login_expired_error()

        if isinstance(exc, FileNotFoundError):
            return FolderNotFoundError(path)

        if exc_name in {"P115InvalidArgumentError"} or isinstance(exc, ValueError):
            return InvalidFolderError(path, reason="115 网盘目录路径无效")

        if "expired" in normalized_message or "cookie" in normalized_message:
            return self._build_login_expired_error()

        if "not found" in normalized_message or "missing" in normalized_message:
            return FolderNotFoundError(path)

        if "invalid" in normalized_message or "bad path" in normalized_message:
            return InvalidFolderError(path, reason="115 网盘目录路径无效")

        return self._build_generic_browse_error()

    async def _call_browse_api(self, func, *args, error_path: str, **kwargs):
        """Wrap p115client browse calls and translate third-party failures."""
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            raise self._map_browse_error(exc, error_path) from exc

    def _virtual_path_to_115_path(self, path: str) -> str:
        """Convert a virtual 115 path into the provider-native directory path."""
        normalized_path = self._normalize_virtual_path(path)
        if normalized_path == VIRTUAL_115_ROOT_PATH:
            return "/"
        suffix = normalized_path.removeprefix(VIRTUAL_115_ROOT_PATH)
        return suffix or "/"

    def _extract_directory_id(self, response: Any) -> str | None:
        """Extract a directory id from fs_dir_getid/getid2 responses."""
        if isinstance(response, dict):
            for key in ("id", "file_id", "cid"):
                value = response.get(key)
                if value not in (None, ""):
                    return str(value)

            data = response.get("data")
            if isinstance(data, dict):
                for key in ("id", "file_id", "cid"):
                    value = data.get(key)
                    if value not in (None, ""):
                        return str(value)
        return None

    async def _resolve_directory_id(
        self,
        *,
        client: Any,
        path: str,
        file_id: str | None,
    ) -> str:
        """Resolve the effective 115 directory id for a browse request."""
        if file_id:
            return str(file_id)

        normalized_path = self._normalize_virtual_path(path)
        if normalized_path == VIRTUAL_115_ROOT_PATH:
            return "0"

        native_path = self._virtual_path_to_115_path(path)
        response = await self._call_browse_api(
            client.fs_dir_getid,
            native_path,
            async_=True,
            error_path=normalized_path,
        )
        directory_id = self._extract_directory_id(response)
        if directory_id:
            return directory_id

        response = await self._call_browse_api(
            client.fs_dir_getid2,
            native_path,
            async_=True,
            error_path=normalized_path,
        )
        directory_id = self._extract_directory_id(response)
        if directory_id:
            return directory_id

        raise FolderNotFoundError(normalized_path)

    async def _browse_async(
        self,
        *,
        path: str,
        file_id: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Load one page of 115 directory entries using persisted cookies."""
        config = await self.config_service.get_115_config()
        if not config.is_logged_in or not config.cookies.strip():
            raise ConfigurationError("请先登录 115 网盘", config_key="cloud_115_config")

        client = await self._load_p115_client_with_config(config)
        normalized_path = self._normalize_virtual_path(path)
        directory_id = await self._resolve_directory_id(
            client=client,
            path=normalized_path,
            file_id=file_id,
        )
        response = await self._call_browse_api(
            client.fs_files,
            {
                "cid": directory_id,
                "offset": max(page - 1, 0) * page_size,
                "limit": page_size,
                "show_dir": 1,
            },
            async_=True,
            error_path=normalized_path,
        )
        current_path, parent_path, current_file_id, parent_file_id = await self._call_browse_api(
            self._resolve_browse_paths,
            client=client,
            response=response,
            directory_id=directory_id,
            requested_path=normalized_path,
            error_path=normalized_path,
        )
        entries = [
            self._normalize_browse_entry(item, current_path)
            for item in response.get("data", [])
            if isinstance(item, dict)
        ]
        return {
            "current_path": current_path,
            "parent_path": parent_path,
            "current_file_id": current_file_id,
            "parent_file_id": parent_file_id,
            "entries": entries,
            "total": self._coerce_int(response.get("count"), len(entries)),
        }

    async def _get_qr_payload(self, uid: str) -> dict[str, Any]:
        """Return the saved QR payload required by p115client."""
        value = await self.config_service.get(self._qr_payload_key(uid), encrypted=True)
        if value:
            try:
                data = json.loads(value)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return {"uid": uid}

    async def _save_qr_payload(
        self,
        uid: str,
        payload: dict[str, Any],
        app: str,
    ) -> None:
        """Persist QR payload so polling survives new service instances."""
        persisted_payload = dict(payload)
        persisted_payload["app"] = app
        await self.config_service.set(
            self._qr_payload_key(uid),
            json.dumps(persisted_payload),
            encrypted=True,
        )

    async def _delete_qr_payload(self, uid: str) -> None:
        """Remove persisted QR payload after successful login."""
        await self.config_service.delete(self._qr_payload_key(uid))

    async def _delete_all_qr_payloads(self) -> None:
        """Remove every persisted QR payload so login cannot resume after logout."""
        await self.config_service._ensure_db()
        async with aiosqlite.connect(self.config_service.db_path) as db:
            await db.execute(
                "DELETE FROM config WHERE substr(key, 1, ?) = ?",
                (len(QRCODE_PAYLOAD_PREFIX), QRCODE_PAYLOAD_PREFIX),
            )
            await db.commit()

    def _extract_cookies(self, result_response: dict[str, Any]) -> str:
        """Extract cookies from p115client QR login response."""
        cookie_data = result_response.get("data", {}).get("cookie")
        if isinstance(cookie_data, dict):
            parts = []
            for key in ("UID", "CID", "SEID", "KID"):
                value = cookie_data.get(key)
                if value:
                    parts.append(f"{key}={value}")
            if parts:
                return "; ".join(parts)
        if isinstance(cookie_data, str):
            return cookie_data.strip().rstrip(";")
        return ""

    def _qr_payload_key(self, uid: str) -> str:
        """Build storage key for a QR login payload."""
        return f"{QRCODE_PAYLOAD_PREFIX}{uid}"

    def _build_scan_status_payload(self, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Return only token fields required for scan-status polling."""
        status_payload = {key: value for key, value in payload.items() if key != "app"}
        return status_payload or {"uid": uid}

    def _has_complete_qr_payload(self, payload: dict[str, Any]) -> bool:
        """Check whether the persisted QR payload has the required token fields."""
        required_keys = ("uid", "time", "sign")
        return all(payload.get(key) for key in required_keys)

    def _resolve_qr_app(self, payload: dict[str, Any], app: str | None) -> str:
        """Prefer the persisted canonical app when resuming QR polling."""
        persisted_app = payload.get("app")
        if isinstance(persisted_app, str) and persisted_app.strip():
            return self._normalize_app(persisted_app)
        return self._normalize_app(app)

    async def _resolve_browse_paths(
        self,
        *,
        client: Any,
        response: dict[str, Any],
        directory_id: str,
        requested_path: str,
    ) -> tuple[str, str | None, str | None, str | None]:
        """Resolve current/parent virtual paths and their file ids from breadcrumbs.

        Returns ``(current_path, parent_path, current_file_id, parent_file_id)``.
        The file ids let callers navigate back to the parent directory without
        keeping a client-side navigation stack.
        """
        breadcrumbs = self._extract_fs_files_breadcrumbs(response)
        if not breadcrumbs and directory_id != "0":
            info = await client.fs_info({"file_id": directory_id}, async_=True)
            breadcrumbs = self._extract_fs_info_breadcrumbs(info)

        current_path = self._breadcrumbs_to_virtual_path(breadcrumbs)
        if not current_path:
            current_path = self._normalize_virtual_path(requested_path)
        if not current_path:
            current_path = VIRTUAL_115_ROOT_PATH

        # 当前目录 file_id：优先面包屑最后一层，否则用请求的 directory_id
        current_file_id: str | None = None
        if breadcrumbs:
            current_file_id = breadcrumbs[-1].get("cid") or str(directory_id)
        else:
            current_file_id = str(directory_id)

        if current_path == VIRTUAL_115_ROOT_PATH:
            # 115 根的上级是本地根（parent_path=None 表示触顶）
            return current_path, None, current_file_id, None

        parent_path = current_path.rsplit("/", 1)[0]
        if not parent_path or parent_path == "/":
            parent_path = VIRTUAL_115_ROOT_PATH

        # 父目录 file_id：面包屑倒数第二个；115 根的父 id 固定为 "0"
        parent_file_id: str | None = None
        if len(breadcrumbs) >= 2:
            parent_file_id = breadcrumbs[-2].get("cid") or "0"
        elif parent_path == VIRTUAL_115_ROOT_PATH:
            parent_file_id = "0"
        return current_path, parent_path, current_file_id, parent_file_id

    def _extract_fs_files_breadcrumbs(self, response: dict[str, Any]) -> list[dict[str, str]]:
        """Translate fs_files path payload into simple breadcrumbs."""
        path_rows = response.get("path")
        if not isinstance(path_rows, list):
            return []
        breadcrumbs: list[dict[str, str]] = []
        for row in path_rows:
            if not isinstance(row, dict):
                continue
            cid = self._pick_first(row, "cid", "file_id", "id")
            if cid is None:
                continue
            breadcrumbs.append(
                {
                    "cid": cid,
                    "pid": self._pick_first(row, "pid", "parent_id") or "0",
                    "name": self._pick_first(row, "name", "file_name", "n") or "",
                }
            )
        return breadcrumbs

    def _extract_fs_info_breadcrumbs(self, response: dict[str, Any]) -> list[dict[str, str]]:
        """Translate fs_info payload into breadcrumbs when fs_files omits them."""
        data = response.get("data", response)
        if not isinstance(data, dict):
            return []
        path_rows = data.get("paths")
        if not isinstance(path_rows, list):
            return []

        breadcrumbs: list[dict[str, str]] = []
        parent_id = "0"
        for row in path_rows:
            if not isinstance(row, dict):
                continue
            cid = self._pick_first(row, "file_id", "cid", "id")
            if cid is None:
                continue
            breadcrumbs.append(
                {
                    "cid": cid,
                    "pid": parent_id,
                    "name": self._pick_first(row, "file_name", "name", "n") or "",
                }
            )
            parent_id = cid

        current_id = self._pick_first(data, "file_id", "cid", "id")
        if (
            current_id is not None
            and breadcrumbs
            and breadcrumbs[-1]["cid"] != current_id
        ):
            breadcrumbs.append(
                {
                    "cid": current_id,
                    "pid": parent_id,
                    "name": self._pick_first(data, "file_name", "name", "n") or "",
                }
            )
        return breadcrumbs

    def _breadcrumbs_to_virtual_path(self, breadcrumbs: list[dict[str, str]]) -> str:
        """Build a virtual 115 path from breadcrumb rows.

        The 115 cloud root (cid == "0") is reported with a display name like
        "根目录", but it IS the virtual root already represented by the
        ``/115网盘`` prefix, so its name must not be appended to the path.
        """
        parts = [
            row["name"]
            for row in breadcrumbs
            if row.get("name") and str(row.get("cid")) != "0"
        ]
        if not parts:
            return VIRTUAL_115_ROOT_PATH if breadcrumbs else ""
        return f"{VIRTUAL_115_ROOT_PATH}/{'/'.join(parts)}"

    def _normalize_browse_entry(
        self,
        entry: dict[str, Any],
        current_path: str,
    ) -> dict[str, Any]:
        """Map raw 115 rows into FileService-compatible entry dicts."""
        is_dir = "fid" not in entry
        file_id = self._pick_first(entry, "cid" if is_dir else "fid", "id") or "0"
        parent_id = self._pick_first(entry, "pid" if is_dir else "cid", "parent_id")
        name = self._pick_first(entry, "n", "name", "file_name") or ""

        return {
            "name": name,
            "path": self._join_virtual_path(current_path, name),
            "is_dir": is_dir,
            "provider": "115",
            "file_id": file_id,
            "parent_id": parent_id,
            "size": None if is_dir else self._coerce_int(entry.get("s") or entry.get("size")),
            "mtime": self._format_timestamp(
                entry.get("te") or entry.get("mtime") or entry.get("user_utime")
            ),
        }

    def _normalize_virtual_path(self, path: str | None) -> str:
        """Normalize incoming provider paths to the virtual 115 root namespace."""
        value = (path or "").strip()
        if not value or value == "/":
            return VIRTUAL_115_ROOT_PATH
        if value == "115网盘":
            return VIRTUAL_115_ROOT_PATH
        if value.startswith(VIRTUAL_115_ROOT_PATH):
            return value.rstrip("/") or VIRTUAL_115_ROOT_PATH
        return f"{VIRTUAL_115_ROOT_PATH}/{value.lstrip('/')}".rstrip("/")

    def _join_virtual_path(self, current_path: str, name: str) -> str:
        """Join a child name onto the current virtual path."""
        base = current_path.rstrip("/") or VIRTUAL_115_ROOT_PATH
        child = name.strip("/")
        return f"{base}/{child}" if child else base

    def _coerce_int(self, value: Any, default: int | None = None) -> int | None:
        """Convert loosely typed numeric values into ints."""
        if value in (None, ""):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _format_timestamp(self, value: Any) -> str | None:
        """Convert epoch-second timestamps into ISO strings."""
        timestamp = self._coerce_int(value)
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp).isoformat()

    def _pick_first(self, payload: dict[str, Any], *keys: str) -> str | None:
        """Return the first non-empty string-like value for the given keys."""
        for key in keys:
            value = payload.get(key)
            if value in (None, ""):
                continue
            return str(value)
        return None

    def _normalize_app(self, app: str | None) -> str:
        """Normalize aliases to canonical app identifiers."""
        value = (app or DEFAULT_APP).strip() or DEFAULT_APP
        if value == "desktop":
            return "web"
        if value == "windows":
            return "os_windows"
        if value == "mac":
            return "os_mac"
        if value == "linux":
            return "os_linux"
        return value
