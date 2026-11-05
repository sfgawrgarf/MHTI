"""Legacy default migration must not overwrite deliberate user settings."""

import json

import pytest

from server.domain.system.config_service import ConfigService, SYSTEM_CONFIG_KEY
from server.models.system import SystemConfig


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['task_timeout', 'scrape_timeout'])
async def test_old_default_migrates_once(temp_db, field):
    service = ConfigService(db_path=temp_db)
    await service.set(SYSTEM_CONFIG_KEY, json.dumps({field: 30, 'retry_count': 1}))
    current = await service.get_system_config()
    assert current.task_timeout == 300 and current.retry_count == 1
    await service.save_system_config(SystemConfig(task_timeout=30))
    assert (await ConfigService(db_path=temp_db).get_system_config()).task_timeout == 30


@pytest.mark.asyncio
async def test_explicit_setting_before_first_read_is_respected(temp_db):
    service = ConfigService(db_path=temp_db)
    await service.save_system_config(SystemConfig(task_timeout=30))
    assert (await service.get_system_config()).task_timeout == 30


@pytest.mark.asyncio
async def test_custom_timeout_is_not_migrated(temp_db):
    service = ConfigService(db_path=temp_db)
    await service.set(SYSTEM_CONFIG_KEY, json.dumps({'task_timeout': 120}))
    assert (await service.get_system_config()).task_timeout == 120


def test_new_default_and_large_file_limit():
    assert SystemConfig().task_timeout == 300
    assert SystemConfig(task_timeout=1800).task_timeout == 1800
