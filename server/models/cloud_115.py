"""115 cloud configuration models."""

from datetime import datetime

from pydantic import BaseModel


class Cloud115Config(BaseModel):
    """115 cloud login configuration."""

    enabled: bool = False
    app: str = "alipaymini"
    cookies: str = ""
    is_logged_in: bool = False
    updated_at: datetime | None = None


class Cloud115DeviceOption(BaseModel):
    """115 login device option."""

    value: str
    label: str
    group: str = "standard"


class Cloud115Status(BaseModel):
    """115 login status."""

    enabled: bool
    app: str
    is_logged_in: bool
    updated_at: datetime | None = None


class Cloud115QrSession(BaseModel):
    """115 QR login session."""

    uid: str
    qrcode_url: str
    app: str


class Cloud115QrStatus(BaseModel):
    """115 QR login polling status."""

    uid: str
    app: str
    status: str
    message: str
    is_logged_in: bool = False
