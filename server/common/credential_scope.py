"""Saved credentials may only be reused for the same service endpoint."""

from urllib.parse import urlsplit


def same_credential_scope(first: str, second: str) -> bool:
    def scope(value: str) -> tuple:
        url = urlsplit(value.strip())
        return (url.scheme.lower(), (url.hostname or "").lower(),
                url.port or (443 if url.scheme.lower() == "https" else 80),
                url.path.rstrip("/"), url.query)
    return scope(first) == scope(second)
