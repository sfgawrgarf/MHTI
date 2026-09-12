"""Database module - centralized database management.

This module provides:
- Connection pool management
- Centralized table schema definitions
- Database initialization and cleanup
- DatabaseMixin for simplified CRUD operations

Usage:
    from server.core.db import get_db, init_database, close_database, DATABASE_PATH
    from server.core.db import db_context, DatabaseMixin
"""

from server.core.db.base_repository import DatabaseMixin
from server.core.db.connection import (
    DATABASE_PATH,
    DatabaseManager,
    close_database,
    configure_connection,
    db_context,
    get_db,
    get_db_manager,
    init_database,
)
from server.core.db.schema import (
    create_all_tables,
    migrate_manual_jobs_table,
    migrate_scrape_jobs_table,
)

__all__ = [
    "DATABASE_PATH",
    "DatabaseManager",
    "DatabaseMixin",
    "close_database",
    "configure_connection",
    "create_all_tables",
    "db_context",
    "get_db",
    "get_db_manager",
    "init_database",
    "migrate_manual_jobs_table",
    "migrate_scrape_jobs_table",
]
