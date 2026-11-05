"""Real-database upgrade of existing passwords without changing session policy."""

import asyncio
import importlib
import sqlite3
from contextlib import asynccontextmanager

import aiosqlite
import pytest
import pytest_asyncio

from server.domain.identity.auth_service import AuthService
from server.infrastructure.db import configure_connection, create_all_tables


@pytest_asyncio.fixture
async def legacy_account(temp_db, monkeypatch):
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        service = AuthService()
        hashed, salt = service._hash_password('legacy-password', iterations=100_000)
        legacy = f'{salt}${hashed}'
        await db.execute('INSERT INTO admin (username, password_hash) VALUES (?, ?)', ('admin', legacy))
        await db.commit()

    class Manager:
        @asynccontextmanager
        async def get_connection(self):
            async with aiosqlite.connect(temp_db) as db:
                await configure_connection(db)
                yield db

    async def manager():
        return Manager()

    monkeypatch.setattr(importlib.import_module('server.domain.identity.auth_service'), 'get_db_manager', manager)
    return service, temp_db, legacy


@pytest.mark.asyncio
async def test_successful_legacy_login_rehashes_in_place(legacy_account):
    service, db_path, legacy = legacy_account
    assert await service.verify_credentials('admin', 'legacy-password')
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute('SELECT password_hash FROM admin WHERE username = ?', ('admin',))
        stored = (await cursor.fetchone())[0]
    assert stored != legacy and stored.startswith('pbkdf2_sha256$600000$')
    assert service._verify_password('legacy-password', stored)


@pytest.mark.asyncio
async def test_wrong_password_does_not_upgrade(legacy_account):
    service, db_path, legacy = legacy_account
    assert not await service.verify_credentials('admin', 'wrong')
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute('SELECT password_hash FROM admin WHERE username = ?', ('admin',))
        assert (await cursor.fetchone())[0] == legacy


@pytest.mark.asyncio
async def test_simultaneous_valid_legacy_logins_both_succeed(legacy_account):
    service, _path, _legacy = legacy_account
    assert await asyncio.gather(*(service.verify_credentials('admin', 'legacy-password') for _ in range(2))) == [True, True]


@pytest.mark.asyncio
async def test_rehash_cannot_restore_a_password_changed_during_login(legacy_account, monkeypatch):
    service, path, _legacy = legacy_account
    formatter = service._format_password_hash

    def concurrent_change(password):
        with sqlite3.connect(path) as db:
            db.execute('UPDATE admin SET password_hash = ? WHERE username = ?',
                       (formatter('changed-password'), 'admin'))
        return formatter(password)

    monkeypatch.setattr(service, '_format_password_hash', concurrent_change)
    assert not await service.verify_credentials('admin', 'legacy-password')
    assert await service.verify_credentials('admin', 'changed-password')


@pytest.mark.parametrize('stored', [
    'pbkdf2_sha256$0$salt$' + 'a' * 64,
    'pbkdf2_sha256$999999999$salt$' + 'a' * 64,
    'pbkdf2_sha256$bad$salt$' + 'a' * 64,
    'pbkdf2_sha256$600000$salt$not-hex',
])
def test_corrupt_hash_parameters_are_rejected_without_work(stored):
    assert not AuthService()._verify_password('password', stored)
