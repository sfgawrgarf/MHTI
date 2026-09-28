"""P115StorageProvider 的回归测试。

回归背景（2026-09-26）：115 扫码登录成功后，浏览/刮削该网盘一律返回
「服务器内部错误」（500）。根因是构造 P115Client 时传了 pinned 库
（requirements.txt 的 0.0.9.6.5.1）已删除的 check_for_relogin / ensure_cookies，
真实库抛 ``TypeError: P115Client.__init__() got an unexpected keyword argument``。
``P115Service`` 侧有假客户端兜底，本适配层此前完全没有测试，同一个错误在这里
静默重复，因此单独锁定构造参数。
"""

from types import SimpleNamespace

import pytest

from server.application.scraping.p115_storage_provider import P115StorageProvider
from server.domain.system.config_service import ConfigService
from server.models.cloud_115 import Cloud115Config
from server.models.storage import StorageLocator, StorageProvider


class StrictFakeP115Client:
    """签名对齐 pinned 版本（0.0.9.6.5.1）的假客户端。

    多余关键字会直接抛 TypeError——只有真实库才会因旧参数报错，
    假客户端若把 kwargs 收下就挡不住回归。
    """

    init_calls: list[dict] = []

    def __init__(
        self,
        cookies: str = "",
        app: str = "",
        app_id: int = 0,
        console_qrcode: bool = True,
    ) -> None:
        StrictFakeP115Client.init_calls.append(
            {
                "cookies": cookies,
                "app": app,
                "app_id": app_id,
                "console_qrcode": console_qrcode,
            }
        )


@pytest.fixture
def storage_provider(config_service: ConfigService) -> P115StorageProvider:
    """提供使用临时数据库的 P115StorageProvider 实例。"""
    return P115StorageProvider(config_service)


def patch_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 p115client 模块替换为假模块（适配层经公开契约函数加载）。"""
    StrictFakeP115Client.init_calls = []
    fake_module = SimpleNamespace(P115Client=StrictFakeP115Client)

    async def fake_load_p115client():
        return fake_module, SimpleNamespace()

    monkeypatch.setattr(
        "server.domain.integration.p115_service.load_p115client",
        fake_load_p115client,
    )


class TestGetClient:
    """客户端构造路径的回归测试。"""

    @pytest.mark.asyncio
    async def test_builds_client_with_pinned_signature(
        self,
        storage_provider: P115StorageProvider,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """只传 cookies/app/console_qrcode，且不带已删除的旧参数。"""
        patch_loader(monkeypatch)
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="harmony",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        client, app = await storage_provider._get_client()

        assert isinstance(client, StrictFakeP115Client)
        assert app == "harmony"
        assert StrictFakeP115Client.init_calls == [
            {
                "cookies": "UID=1; CID=2; SEID=3; KID=4",
                "app": "harmony",
                "app_id": 0,
                "console_qrcode": False,
            }
        ]

    @pytest.mark.asyncio
    async def test_falls_back_to_default_app(
        self,
        storage_provider: P115StorageProvider,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """配置里没写设备时用默认端，避免构造出「空 app」。"""
        patch_loader(monkeypatch)
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        _client, app = await storage_provider._get_client()

        assert app == "alipaymini"
        assert StrictFakeP115Client.init_calls[-1]["app"] == "alipaymini"

    @pytest.mark.asyncio
    async def test_rejects_missing_login_before_building_client(
        self,
        storage_provider: P115StorageProvider,
        config_service: ConfigService,
    ):
        """未登录（或 cookies 为空）时不构造客户端，直接报「请先登录」。"""
        await config_service.save_115_config(
            Cloud115Config(enabled=True, app="harmony", cookies="   ", is_logged_in=False)
        )

        with pytest.raises(ValueError, match="请先登录"):
            await storage_provider._get_client()


@pytest.mark.asyncio
async def test_find_subdir_id_reads_later_directory_pages(storage_provider) -> None:
    calls: list[int] = []

    class PagedClient:
        async def fs_files(self, payload: dict, async_: bool = False) -> dict:
            calls.append(payload["offset"])
            if payload["offset"] == 0:
                return {"data": [{"n": f"other-{i}", "cid": str(i)} for i in range(100)]}
            return {"data": [{"n": "target", "cid": "target-1"}]}

    result = await storage_provider._find_subdir_id(PagedClient(), "parent", "target")

    assert result == "target-1"
    assert calls == [0, 100]


@pytest.mark.asyncio
async def test_find_file_ids_reads_later_directory_pages(storage_provider) -> None:
    calls: list[int] = []

    class PagedClient:
        async def fs_files(self, payload: dict, async_: bool = False) -> dict:
            calls.append(payload["offset"])
            if payload["offset"] == 0:
                return {"data": [{"n": f"other-{i}", "fid": str(i)} for i in range(100)]}
            return {
                "data": [
                    {"n": "episode.mkv", "fid": "later-1"},
                    {"n": "episode.mkv", "fid": "later-2"},
                ]
            }

    result = await storage_provider._find_file_ids_in_dir(
        PagedClient(), "parent", "episode.mkv"
    )

    assert result == {"later-1", "later-2"}
    assert calls == [0, 100]


@pytest.mark.asyncio
async def test_directory_read_failure_is_not_treated_as_empty(storage_provider) -> None:
    class FailingClient:
        async def fs_files(self, payload: dict, async_: bool = False) -> dict:
            raise RuntimeError("temporary provider failure")

    with pytest.raises(ValueError, match="115 目录读取失败"):
        await storage_provider._find_file_ids_in_dir(
            FailingClient(), "parent", "episode.mkv"
        )


@pytest.mark.asyncio
async def test_copy_does_not_copy_when_initial_snapshot_fails(
    storage_provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingClient:
        copy_calls = 0

        async def fs_files(self, payload: dict, async_: bool = False) -> dict:
            raise RuntimeError("temporary provider failure")

        async def fs_copy(self, file_id: str, pid: str, async_: bool = False) -> dict:
            self.copy_calls += 1
            return {"state": True}

    client = FailingClient()

    async def fake_get_client() -> tuple[FailingClient, str]:
        return client, "harmony"

    monkeypatch.setattr(storage_provider, "_get_client", fake_get_client)
    locator = StorageLocator(
        provider=StorageProvider.P115,
        path="/115网盘/待整理/episode.mkv",
        file_id="source-1",
        parent_id="source-parent",
        is_dir=False,
    )

    with pytest.raises(ValueError, match="115 目录读取失败"):
        await storage_provider.copy(
            locator,
            "episode.mkv",
            "target-parent",
        )

    assert client.copy_calls == 0
