"""Shared file filters used by manual jobs and folder watchers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeConfig


@dataclass(frozen=True)
class OrganizeFilter:
    """Normalized, side-effect-free file filtering rules."""

    min_size_bytes: int = 0
    extensions: frozenset[str] = frozenset()
    filename_blacklist: tuple[str, ...] = ()
    junk_patterns: tuple[str, ...] = ()

    @classmethod
    def from_global_config(cls, config: OrganizeConfig) -> "OrganizeFilter":
        """Build filters from the persisted global organize configuration."""
        return cls(
            min_size_bytes=max(config.min_file_size_mb, 0) * 1024 * 1024,
            extensions=_normalize_extensions(config.file_type_whitelist),
            filename_blacklist=_normalize_terms(config.filename_blacklist),
            junk_patterns=tuple(pattern for pattern in config.junk_pattern_filter if pattern),
        )

    @classmethod
    def from_task_settings(
        cls, settings: ManualJobAdvancedSettings
    ) -> "OrganizeFilter":
        """Build filters from the task-level organize settings.

        The frontend exposes these fields whenever global organize settings are
        disabled.  ``scan_filters_enabled`` is retained for old API clients,
        but a non-empty custom value must still take effect because older
        clients never sent that flag.
        """
        extensions = settings.file_ext_whitelist + settings.extra_ext_whitelist
        return cls(
            min_size_bytes=max(settings.file_size_filter, 0) * 1024 * 1024,
            extensions=_normalize_extensions(extensions),
            filename_blacklist=_normalize_terms(settings.file_name_blacklist),
            junk_patterns=tuple(
                pattern for pattern in settings.file_sanitize_list if pattern
            ),
        )

    def allows(self, filename: str, size: int) -> bool:
        """Return whether a discovered video should be dispatched."""
        if size < self.min_size_bytes:
            return False

        base_name = Path(filename).name
        suffix = Path(base_name).suffix.lower().lstrip(".")
        if self.extensions and suffix not in self.extensions:
            return False

        lowered_name = base_name.casefold()
        if any(term in lowered_name for term in self.filename_blacklist):
            return False
        return True

    def sanitize_filename(self, filename: str) -> str:
        """Remove configured advertising/noise patterns from a filename.

        Invalid user-entered regular expressions are ignored per-pattern so a
        typo cannot stop an otherwise valid scrape job.
        """
        sanitized = filename
        for pattern in self.junk_patterns:
            try:
                sanitized = re.sub(pattern, "", sanitized)
            except re.error:
                continue
        return sanitized


def _normalize_extensions(values: Iterable[str]) -> frozenset[str]:
    return frozenset(
        value.strip().lower().lstrip(".")
        for value in values
        if value and value.strip()
    )


def _normalize_terms(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        value.strip().casefold()
        for value in values
        if value and value.strip()
    )
