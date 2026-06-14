"""Unit tests for P115Service."""

import aiosqlite
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

from server.core.container import Services, get_container, get_service
from server.core.exceptions import ConfigurationError, FolderNotFoundError, InvalidFolderError
from server.models.cloud_115 import Cloud115Config
from server.services.config_service import ConfigService
import server.services.p115_service as p115_service_module
from server.services.p115_service import P115Service


@pytest.fixture
def p115_service(config_service: ConfigService) -> P115Service:
    """Provide a P115Service instance with test config storage."""
    return P115Service(config_service=config_service)


class FakeP115Client:
    """Minimal fake p115 client for QR login tests."""

    init_calls: list[dict] = []
    token_calls: list[dict[str, str | None]] = []
    status_calls: list[dict] = []
    result_calls: list[dict] = []
    fs_files_calls: list[dict] = []
    fs_info_calls: list[dict] = []
    fs_dir_getid_calls: list[dict] = []
    fs_files_response: dict = {"path": [{"cid": "0", "pid": "0", "name": ""}], "count": 0, "data": []}
    fs_info_response: dict = {
        "data": {
            "file_id": "0",
            "file_name": "",
            "sha1": "",
            "paths": [{"file_id": "0", "file_name": ""}],
        }
    }
    fs_dir_getid_response: dict = {"id": "0"}

    def __init__(
        self,
        cookies: str = "",
        check_for_relogin: bool = False,
        ensure_cookies: bool = False,
        app: str | None = None,
        console_qrcode: bool = True,
    ) -> None:
        self.cookies_str = SimpleNamespace(cookies=cookies)
        self.cookies: dict[str, str] = {}
        FakeP115Client.init_calls.append(
            {
                "cookies": cookies,
                "check_for_relogin": check_for_relogin,
                "ensure_cookies": ensure_cookies,
                "app": app,
                "console_qrcode": console_qrcode,
            }
        )

    @staticmethod
    async def _token_response(app: str) -> dict:
        return {
            "data": {
                "uid": "uid-123",
                "time": 1710000000,
                "sign": "sign-123",
                "qrcode": "https://115.com/scan/dg-uid-123",
            }
        }

    def login_qrcode_token(self=None, /, app: str = "web", async_: bool = False) -> dict:
        FakeP115Client.token_calls.append({"self": self, "app": app, "async_": async_})
        if async_:
            return FakeP115Client._token_response(app)
        return {
            "data": {
                "uid": "uid-123",
                "time": 1710000000,
                "sign": "sign-123",
                "qrcode": "https://115.com/scan/dg-uid-123",
            }
        }

    @staticmethod
    async def _status_response(payload: dict) -> dict:
        assert payload["uid"] == "uid-123"
        assert payload["sign"] == "sign-123"
        return {"data": {"status": 2}}

    def login_qrcode_scan_status(payload: dict, async_: bool = False) -> dict:
        FakeP115Client.status_calls.append({"payload": dict(payload), "async_": async_})
        if async_:
            return FakeP115Client._status_response(payload)
        return {"data": {"status": 2}}

    @staticmethod
    async def _result_response(uid: str, app: str, cookies=None) -> dict:
        assert uid == "uid-123"
        assert app == "alipaymini"
        return {
            "data": {
                "status": 2,
                "cookie": {
                    "UID": "1",
                    "CID": "2",
                    "SEID": "3",
                },
            }
        }

    def login_qrcode_scan_result(uid: str, app: str, cookies=None, async_: bool = False) -> dict:
        FakeP115Client.result_calls.append(
            {"uid": uid, "app": app, "cookies": cookies, "async_": async_}
        )
        if async_:
            return FakeP115Client._result_response(uid, app, cookies=cookies)
        return {
            "data": {
                "status": 2,
                "cookie": {
                    "UID": "1",
                    "CID": "2",
                    "SEID": "3",
                },
            }
        }

    @staticmethod
    async def _fs_files_response(payload: dict) -> dict:
        return FakeP115Client.fs_files_response

    def fs_files(self, payload: dict, async_: bool = False) -> dict:
        FakeP115Client.fs_files_calls.append({"payload": dict(payload), "async_": async_})
        if async_:
            return type(self)._fs_files_response(payload)
        return FakeP115Client.fs_files_response

    @staticmethod
    async def _fs_info_response(payload: dict) -> dict:
        return FakeP115Client.fs_info_response

    def fs_info(self, payload: dict, async_: bool = False) -> dict:
        FakeP115Client.fs_info_calls.append({"payload": dict(payload), "async_": async_})
        if async_:
            return type(self)._fs_info_response(payload)
        return FakeP115Client.fs_info_response

    @staticmethod
    async def _fs_dir_getid_response(payload: str | dict) -> dict:
        return FakeP115Client.fs_dir_getid_response

    def fs_dir_getid(self, payload: str | dict, async_: bool = False, **kwargs) -> dict:
        FakeP115Client.fs_dir_getid_calls.append(
            {"payload": payload, "async_": async_, "kwargs": dict(kwargs)}
        )
        if async_:
            return type(self)._fs_dir_getid_response(payload)
        return FakeP115Client.fs_dir_getid_response


def build_fake_p115_module() -> tuple[SimpleNamespace, SimpleNamespace]:
    """Build fake p115 module and const module."""
    const_module = SimpleNamespace(
        AVAILABLE_APPS={
            "web": "115生活_网页端",
            "ios": "115生活_苹果端",
            "alipaymini": "115生活_支付宝小程序",
            "os_windows": "115生活_Windows端",
            "os_mac": "115生活_macOS端",
            "os_linux": "115生活_Linux端",
        },
        APP_TO_SSOENT={
            "web": "A1",
            "desktop": "A1",
            "ios": "D1",
            "bios": "D2",
            "android": "F1",
            "bandroid": "F2",
            "ipad": "H1",
            "bipad": "H2",
            "os_windows": "P1",
            "windows": "P1",
            "os_mac": "P2",
            "mac": "P2",
            "os_linux": "P3",
            "linux": "P3",
            "alipaymini": "R2",
        },
        SSOENT_TO_APP={
            "A1": "web",
            "D1": "ios",
            "P1": "os_windows",
            "P2": "os_mac",
            "P3": "os_linux",
            "R2": "alipaymini",
        },
    )
    return SimpleNamespace(P115Client=FakeP115Client), const_module


class TestP115Service:
    """Tests for P115Service."""

    def test_list_login_devices_includes_standard_apps_and_aliases(
        self,
        p115_service,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Devices list should expose standard apps and common aliases."""
        fake_module, fake_const = build_fake_p115_module()
        monkeypatch.setattr(
            "server.services.p115_service._load_p115client_sync",
            lambda: (fake_module, fake_const),
        )

        items = p115_service.list_login_devices()
        items_by_value = {item.value: item for item in items}

        assert "web" in items_by_value
        assert "alipaymini" in items_by_value
        assert "os_windows" in items_by_value
        assert "desktop" in items_by_value
        assert "bios" in items_by_value
        assert "bandroid" in items_by_value
        assert "bipad" in items_by_value
        assert "windows" in items_by_value
        assert "mac" in items_by_value
        assert "linux" in items_by_value
        assert items_by_value["web"].group == "standard"
        assert items_by_value["alipaymini"].group == "standard"
        assert items_by_value["desktop"].group == "alias"
        assert items_by_value["bios"].group == "alias"
        assert items_by_value["bandroid"].group == "alias"
        assert items_by_value["bipad"].group == "alias"
        assert items_by_value["windows"].group == "alias"
        assert items_by_value["mac"].group == "alias"
        assert items_by_value["linux"].group == "alias"
        assert items_by_value["alipaymini"].label != "alipaymini"

    @pytest.mark.asyncio
    async def test_get_status_uses_saved_115_config(self, config_service: ConfigService):
        """Status should be derived from saved 115 config."""
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        status = await service.get_status()

        assert status.enabled is True
        assert status.app == "alipaymini"
        assert status.is_logged_in is True

    @pytest.mark.asyncio
    async def test_get_status_defaults_to_alipaymini(self, config_service: ConfigService):
        """Status should default app to alipaymini when no config exists."""
        service = P115Service(config_service=config_service)

        status = await service.get_status()

        assert status.app == "alipaymini"
        assert status.enabled is False
        assert status.is_logged_in is False

    @pytest.mark.asyncio
    async def test_start_and_poll_qr_login_save_cookies(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Successful QR login should persist cookies back to ConfigService."""
        fake_module, fake_const = build_fake_p115_module()
        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)

        session = await service.start_qr_login("alipaymini")
        poll_status = await service.poll_qr_login(session.uid, session.app)
        saved_config = await config_service.get_115_config()

        assert session.uid == "uid-123"
        assert session.qrcode_url == "https://115.com/scan/dg-uid-123"
        assert poll_status.status == "success"
        assert poll_status.is_logged_in is True
        assert fake_module.P115Client.token_calls[-1]["async_"] is True
        assert fake_module.P115Client.status_calls[-1]["async_"] is True
        assert fake_module.P115Client.result_calls[-1]["async_"] is True
        assert saved_config.enabled is True
        assert saved_config.app == "alipaymini"
        assert saved_config.cookies == "UID=1; CID=2; SEID=3"
        assert saved_config.is_logged_in is True

    @pytest.mark.asyncio
    async def test_start_qr_login_passes_normalized_app_with_real_calling_convention(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """QR token generation should receive the normalized selected app."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)

        session = await service.start_qr_login("windows")

        assert session.app == "os_windows"
        assert fake_module.P115Client.token_calls == [
            {"self": None, "app": "os_windows", "async_": True}
        ]

    @pytest.mark.asyncio
    async def test_start_qr_login_persists_qr_payload_encrypted(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Pending QR payload should be stored encrypted instead of plaintext."""
        fake_module, fake_const = build_fake_p115_module()

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)

        session = await service.start_qr_login("windows")

        async with aiosqlite.connect(config_service.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT value, encrypted FROM config WHERE key = ?",
                (service._qr_payload_key(session.uid),),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["encrypted"] == 1
        assert row["value"] != (
            '{"uid": "uid-123", "time": 1710000000, "sign": "sign-123", '
            '"qrcode": "https://115.com/scan/dg-uid-123", "app": "os_windows"}'
        )
        assert "sign-123" not in row["value"]
        assert "os_windows" not in row["value"]

    @pytest.mark.asyncio
    async def test_poll_qr_login_works_with_fresh_service_instance(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Polling should still work after creating a fresh service instance."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []
        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )

        first_service = P115Service(config_service=config_service)
        session = await first_service.start_qr_login("alipaymini")

        second_service = P115Service(config_service=config_service)
        poll_status = await second_service.poll_qr_login(session.uid, session.app)

        assert poll_status.status == "success"
        assert poll_status.is_logged_in is True
        assert fake_module.P115Client.status_calls[-1]["payload"]["time"] == 1710000000
        assert fake_module.P115Client.status_calls[-1]["payload"]["sign"] == "sign-123"

    @pytest.mark.asyncio
    async def test_poll_qr_login_prefers_persisted_canonical_app_after_restart(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Polling after restart should keep using the persisted canonical app."""

        class WindowsQrFakeP115Client(FakeP115Client):
            def login_qrcode_scan_result(
                uid: str,
                app: str,
                cookies=None,
                async_: bool = False,
            ) -> dict:
                WindowsQrFakeP115Client.result_calls.append(
                    {"uid": uid, "app": app, "cookies": cookies, "async_": async_}
                )
                if async_:
                    return WindowsQrFakeP115Client._result_response(
                        uid,
                        app,
                        cookies=cookies,
                    )
                return {
                    "data": {
                        "status": 2,
                        "cookie": {
                            "UID": "1",
                            "CID": "2",
                            "SEID": "3",
                        },
                    }
                }

            @staticmethod
            async def _result_response(uid: str, app: str, cookies=None) -> dict:
                assert uid == "uid-123"
                assert app == "os_windows"
                return {
                    "data": {
                        "status": 2,
                        "cookie": {
                            "UID": "1",
                            "CID": "2",
                            "SEID": "3",
                        },
                    }
                }

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=WindowsQrFakeP115Client)
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )

        first_service = P115Service(config_service=config_service)
        session = await first_service.start_qr_login("windows")

        second_service = P115Service(config_service=config_service)
        poll_status = await second_service.poll_qr_login(session.uid, "alipaymini")
        saved_config = await config_service.get_115_config()

        assert session.app == "os_windows"
        assert poll_status.status == "success"
        assert poll_status.app == "os_windows"
        assert poll_status.is_logged_in is True
        assert fake_module.P115Client.result_calls[-1]["app"] == "os_windows"
        assert saved_config.app == "os_windows"
        assert saved_config.cookies == "UID=1; CID=2; SEID=3"

    @pytest.mark.asyncio
    async def test_poll_qr_login_does_not_report_success_without_cookies(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Missing cookies in scan result must not be treated as login success."""

        class NoCookieFakeP115Client(FakeP115Client):
            def login_qrcode_scan_result(
                uid: str,
                app: str,
                cookies=None,
                async_: bool = False,
            ) -> dict:
                FakeP115Client.result_calls.append(
                    {"uid": uid, "app": app, "cookies": cookies, "async_": async_}
                )
                if async_:
                    return NoCookieFakeP115Client._result_response(
                        uid,
                        app,
                        cookies=cookies,
                    )
                return {"data": {"status": 2}}

            @staticmethod
            async def _result_response(uid: str, app: str, cookies=None) -> dict:
                assert uid == "uid-123"
                assert app == "alipaymini"
                return {"data": {"status": 2}}

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=NoCookieFakeP115Client)
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)
        await service.start_qr_login(None)

        poll_status = await service.poll_qr_login("uid-123", "")
        saved_config = await config_service.get_115_config()

        assert poll_status.status != "success"
        assert poll_status.is_logged_in is False
        assert saved_config == Cloud115Config()

    @pytest.mark.asyncio
    async def test_poll_qr_login_returns_missing_session_without_persisted_payload(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Missing QR payload should short-circuit locally without remote polling."""

        async def fail_if_loader_called():
            raise AssertionError("p115client loader should not be called without QR payload")

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fail_if_loader_called,
        )

        service = P115Service(config_service=config_service)

        poll_status = await service.poll_qr_login("uid-missing", "alipaymini")

        assert poll_status.uid == "uid-missing"
        assert poll_status.app == "alipaymini"
        assert poll_status.status == "expired"
        assert poll_status.is_logged_in is False
        assert "会话" in poll_status.message

    @pytest.mark.asyncio
    async def test_browse_returns_provider_entries_for_root_directory(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Browse should map 115 root entries into provider-aware rows."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.init_calls = []
        fake_module.P115Client.fs_files_calls = []
        fake_module.P115Client.fs_info_calls = []
        fake_module.P115Client.fs_dir_getid_calls = []
        fake_module.P115Client.fs_files_response = {
            "path": [{"cid": "0", "pid": "0", "name": ""}],
            "count": 2,
            "data": [
                {"cid": "100", "pid": "0", "n": "电影", "te": "1710000000"},
                {"fid": "200", "cid": "0", "n": "Movie.mkv", "s": "123", "te": "1710000001"},
            ],
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        result = await service.browse(path="/115网盘", file_id="0", page=2, page_size=10)
        expected_dir_mtime = datetime.fromtimestamp(1710000000).isoformat()
        expected_file_mtime = datetime.fromtimestamp(1710000001).isoformat()

        assert fake_module.P115Client.init_calls[-1] == {
            "cookies": "UID=1; CID=2; SEID=3; KID=4",
            "check_for_relogin": False,
            "ensure_cookies": False,
            "app": "alipaymini",
            "console_qrcode": False,
        }
        assert fake_module.P115Client.fs_files_calls == [
            {
                "payload": {"cid": "0", "offset": 10, "limit": 10, "show_dir": 1},
                "async_": True,
            }
        ]
        assert result["current_path"] == "/115网盘"
        assert result["parent_path"] is None
        assert result["total"] == 2
        assert result["entries"] == [
            {
                "name": "电影",
                "path": "/115网盘/电影",
                "is_dir": True,
                "provider": "115",
                "file_id": "100",
                "parent_id": "0",
                "size": None,
                "mtime": expected_dir_mtime,
            },
            {
                "name": "Movie.mkv",
                "path": "/115网盘/Movie.mkv",
                "is_dir": False,
                "provider": "115",
                "file_id": "200",
                "parent_id": "0",
                "size": 123,
                "mtime": expected_file_mtime,
            },
        ]

    @pytest.mark.asyncio
    async def test_browse_resolves_directory_id_from_virtual_path_when_file_id_missing(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Virtual 115 subpaths should be resolved to a directory id before browsing."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.fs_dir_getid_calls = []
        fake_module.P115Client.fs_files_calls = []
        fake_module.P115Client.fs_dir_getid_response = {"id": "100"}
        fake_module.P115Client.fs_files_response = {
            "path": [
                {"cid": "0", "pid": "0", "name": ""},
                {"cid": "100", "pid": "0", "name": "电影"},
            ],
            "count": 1,
            "data": [
                {"cid": "300", "pid": "100", "n": "动作片"},
            ],
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        result = await service.browse(path="/115网盘/电影", file_id=None, page=1, page_size=5)

        assert fake_module.P115Client.fs_dir_getid_calls == [
            {"payload": "/电影", "async_": True, "kwargs": {}}
        ]
        assert fake_module.P115Client.fs_files_calls == [
            {
                "payload": {"cid": "100", "offset": 0, "limit": 5, "show_dir": 1},
                "async_": True,
            }
        ]
        assert result["current_path"] == "/115网盘/电影"
        assert result["entries"][0]["path"] == "/115网盘/电影/动作片"

    @pytest.mark.asyncio
    async def test_browse_uses_fs_info_to_complete_subdirectory_paths(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Browse should fall back to fs_info when fs_files lacks breadcrumb data."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.fs_files_calls = []
        fake_module.P115Client.fs_info_calls = []
        fake_module.P115Client.fs_files_response = {
            "count": 1,
            "data": [
                {"cid": "300", "pid": "100", "n": "动作片"},
            ],
        }
        fake_module.P115Client.fs_info_response = {
            "data": {
                "file_id": "100",
                "file_name": "电影",
                "sha1": "",
                "paths": [
                    {"file_id": "0", "file_name": ""},
                    {"file_id": "100", "file_name": "电影"},
                ],
            }
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        result = await service.browse(path="", file_id="100", page=1, page_size=5)

        assert fake_module.P115Client.fs_files_calls == [
            {
                "payload": {"cid": "100", "offset": 0, "limit": 5, "show_dir": 1},
                "async_": True,
            }
        ]
        assert fake_module.P115Client.fs_info_calls == [
            {"payload": {"file_id": "100"}, "async_": True}
        ]
        assert result["current_path"] == "/115网盘/电影"
        assert result["parent_path"] == "/115网盘"
        assert result["entries"] == [
            {
                "name": "动作片",
                "path": "/115网盘/电影/动作片",
                "is_dir": True,
                "provider": "115",
                "file_id": "300",
                "parent_id": "100",
                "size": None,
                "mtime": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_browse_raises_configuration_error_when_not_logged_in(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Browse should fail with an application error when 115 cookies are unavailable."""

        async def fail_if_loader_called():
            raise AssertionError("p115client loader should not be called without cookies")

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fail_if_loader_called,
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="115"):
            await service.browse(path="/115网盘", file_id="0", page=1, page_size=20)

    @pytest.mark.asyncio
    async def test_browse_converts_fs_files_errors_to_configuration_error(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Authentication failures should surface as a login-expired business error."""

        class FsFilesErrorFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_files_response(payload: dict) -> dict:
                raise RuntimeError("cookies expired")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=FsFilesErrorFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="重新扫码登录") as exc_info:
            await service.browse(path="/115网盘", file_id="0", page=1, page_size=20)

        assert exc_info.value.details["config_key"] == "cloud_115_config"

    @pytest.mark.asyncio
    async def test_browse_converts_missing_directory_to_folder_not_found(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Directory lookup failures should not be misreported as login expiration."""

        class MissingDirectoryFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_dir_getid_response(payload: str | dict) -> dict:
                raise FileNotFoundError("directory missing")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=MissingDirectoryFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(FolderNotFoundError, match="电影"):
            await service.browse(path="/115网盘/电影", file_id=None, page=1, page_size=20)

    @pytest.mark.asyncio
    async def test_browse_converts_invalid_directory_arguments_to_invalid_folder(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Invalid browse arguments should not be translated into relogin guidance."""

        class InvalidDirectoryFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_dir_getid_response(payload: str | dict) -> dict:
                raise ValueError("bad path")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=InvalidDirectoryFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(InvalidFolderError, match="路径"):
            await service.browse(path="/115网盘/电影", file_id=None, page=1, page_size=20)

    @pytest.mark.asyncio
    async def test_browse_converts_unknown_provider_errors_to_generic_configuration_error(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Unknown provider failures should surface as a generic browse error."""

        class UnknownBrowseErrorFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_info_response(payload: dict) -> dict:
                raise RuntimeError("upstream unavailable")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=UnknownBrowseErrorFakeP115Client)
        fake_module.P115Client.fs_files_response = {
            "count": 1,
            "data": [
                {"cid": "300", "pid": "100", "n": "动作片"},
            ],
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="目录浏览失败") as exc_info:
            await service.browse(path="", file_id="100", page=1, page_size=20)

        assert exc_info.value.details["config_key"] == "cloud_115_config"

    @pytest.mark.asyncio
    async def test_scan_folder_recursively_collects_video_files(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """scan_folder should walk sub-directories and keep only video files."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.fs_files_calls = []

        # Two pages on the root: first page full (2 items, page_size=100 -> treat <100 as last page).
        # We return one page for root, one for the nested dir, by switching on cid.
        root_response = {
            "path": [{"cid": "0", "pid": "0", "name": ""}],
            "count": 2,
            "data": [
                {"cid": "100", "pid": "0", "n": "剧集"},
                {"fid": "200", "cid": "0", "n": "readme.txt", "s": "10", "te": "1710000000"},
            ],
        }
        nested_response = {
            "path": [
                {"cid": "0", "pid": "0", "name": ""},
                {"cid": "100", "pid": "0", "name": "剧集"},
            ],
            "count": 2,
            "data": [
                {"fid": "300", "cid": "100", "n": "S01E01.mkv", "s": "12345", "te": "1710000001"},
                {"fid": "301", "cid": "100", "n": "S01E02.mp4", "s": "12346", "te": "1710000002"},
            ],
        }

        class ScanFakeP115Client(FakeP115Client):
            def fs_files(self, payload: dict, async_: bool = False) -> dict:
                FakeP115Client.fs_files_calls.append({"payload": dict(payload), "async_": async_})
                cid = payload.get("cid")
                response = nested_response if cid == "100" else root_response
                if async_:
                    return type(self)._static_response(response)
                return response

            @staticmethod
            async def _static_response(response):
                return response

        fake_module = SimpleNamespace(P115Client=ScanFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        entries = await service.scan_folder(path="/115网盘", file_id="0")

        # Only video files kept; readme.txt filtered out.
        names = [e["name"] for e in entries]
        assert names == ["S01E01.mkv", "S01E02.mp4"]
        # Each entry carries enough to rebuild a StorageLocator.
        for entry in entries:
            assert entry["provider"] == "115"
            assert entry["is_dir"] is False
            assert entry["file_id"] in {"300", "301"}
            assert entry["parent_id"] == "100"
            assert entry["path"].startswith("/115网盘/剧集/")

    @pytest.mark.asyncio
    async def test_scan_folder_raises_configuration_error_when_not_logged_in(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """scan_folder should refuse when 115 cookies are unavailable."""

        async def fail_if_loader_called():
            raise AssertionError("loader should not be called without cookies")

        monkeypatch.setattr(
            "server.services.p115_service._load_p115client",
            fail_if_loader_called,
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="请先登录"):
            await service.scan_folder(path="/115网盘", file_id="0")

    def test_load_p115client_sync_restores_home_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Real p115client loader should restore HOME/USERPROFILE after import."""
        original_cache = p115_service_module._P115_MODULE_CACHE
        monkeypatch.setenv("HOME", "C:/tmp/test-home")
        monkeypatch.setenv("USERPROFILE", "C:/tmp/test-userprofile")
        p115_service_module._P115_MODULE_CACHE = None

        try:
            p115_module, const_module = p115_service_module._load_p115client_sync()
        finally:
            p115_service_module._P115_MODULE_CACHE = original_cache

        assert getattr(p115_module, "__name__", "") == "p115client"
        assert getattr(const_module, "__name__", "") == "p115client.const"
        assert os.environ["HOME"] == "C:/tmp/test-home"
        assert os.environ["USERPROFILE"] == "C:/tmp/test-userprofile"

    def test_container_get_service_resolves_p115_with_config_dependency(
        self,
        config_service: ConfigService,
    ):
        """Container runtime path should resolve P115Service without crashing."""
        container = get_container()
        container.clear()
        try:
            container.register_instance(Services.CONFIG, config_service)

            service = get_service(Services.P115)

            assert isinstance(service, P115Service)
            assert service.config_service is config_service
        finally:
            container.clear()
