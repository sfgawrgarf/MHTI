"""Regression tests for spreadsheet-safe CSV exports."""

import csv
import io
from datetime import datetime
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from server.core.db import configure_connection, create_all_tables
from server.models.history import HistoryRecordCreate, TaskStatus
from server.models.log import LogEntry, LogLevel
from server.services.history_service import HistoryService
from server.services.log_service import LogService


@pytest.mark.asyncio
async def test_log_csv_neutralizes_formulas_and_uses_csv_escaping() -> None:
    service = LogService()
    service.get_logs = AsyncMock(
        return_value=(
            [
                LogEntry(
                    id=1,
                    timestamp=datetime(2026, 1, 1),
                    level=LogLevel.ERROR,
                    logger="=CMD()",
                    message='+HYPERLINK("https://example.test","x"),next',
                    request_id="  @SUM(1,1)",
                    user_id=7,
                )
            ],
            1,
        )
    )

    exported = await service.export_logs(format="csv")
    row = list(csv.reader(io.StringIO(exported)))[1]

    assert row[2] == "'=CMD()"
    assert row[3].startswith("'+HYPERLINK")
    assert row[3].endswith(",next")
    assert row[4] == "'  @SUM(1,1)"


@pytest.mark.asyncio
async def test_history_csv_neutralizes_untrusted_record_fields(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()

    service = HistoryService(temp_db)
    await service.create_record(
        HistoryRecordCreate(
            task_name="=CMD()",
            folder_path="  +HYPERLINK()",
            status=TaskStatus.FAILED,
            total_files=1,
            success_count=0,
            failed_count=1,
            duration_seconds=0,
            error_message="\t=malicious",
        )
    )

    exported = await service.export_csv()
    row = list(csv.reader(io.StringIO(exported)))[1]

    assert row[1] == "'=CMD()"
    assert row[2] == "'  +HYPERLINK()"
    assert row[9] == "'\t=malicious"
