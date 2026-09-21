"""CSV export helpers that prevent spreadsheet formula execution."""

from __future__ import annotations


FORMULA_PREFIXES = ("=", "+", "-", "@")
CONTROL_PREFIXES = ("\t", "\r", "\n")


def neutralize_csv_formula(value: str) -> str:
    """Prefix spreadsheet-like formulas while preserving the displayed text."""
    if not value:
        return value
    if value.startswith(CONTROL_PREFIXES) or value.lstrip().startswith(FORMULA_PREFIXES):
        return "'" + value
    return value
