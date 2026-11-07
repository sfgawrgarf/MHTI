"""Apply persisted logging settings to the handlers used by the application."""

import logging
from logging.handlers import RotatingFileHandler

from server.models.log import LogConfig


class LoggingRuntime:
    def __init__(self, root: logging.Logger, file_handler: RotatingFileHandler | None,
                 database_handler: logging.Handler) -> None:
        self.root = root
        self.file_handler = file_handler
        self.database_handler = database_handler
        self.console_handlers = [handler for handler in root.handlers
                                 if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)]

    def apply(self, config: LogConfig) -> None:
        level = getattr(logging, config.log_level.value)
        disabled = logging.CRITICAL + 1
        self.root.setLevel(level)
        for handler in self.console_handlers:
            handler.setLevel(level if config.console_enabled else disabled)
        if self.file_handler is not None:
            self.file_handler.acquire()
            try:
                self.file_handler.maxBytes = config.max_file_size_mb * 1024 * 1024
                self.file_handler.backupCount = config.max_file_count
                self.file_handler.setLevel(level if config.file_enabled else disabled)
            finally:
                self.file_handler.release()
        self.database_handler.setLevel(level if config.db_enabled else disabled)
