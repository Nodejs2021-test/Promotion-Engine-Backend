"""Sign-in, registration and first-run setup request models."""

from pydantic import BaseModel, Field

from app.modules.settings.schema import AppSettingsIn


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class SetupAdmin(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.\-]+$")
    full_name: str = Field(min_length=1, max_length=120)
    email: str | None = None
    password: str = Field(min_length=8, max_length=128)


class SetupIn(AppSettingsIn):
    admin: SetupAdmin


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.\-]+$")
    full_name: str = Field(min_length=1, max_length=120)
    email: str | None = None
    password: str = Field(min_length=8, max_length=128)
    # Required only for the very first account, which also sets up the organisation.
    organization_name: str | None = Field(None, min_length=1, max_length=120)
    currency: str | None = Field(None, pattern=r"^[A-Za-z]{3}$")
    timezone: str | None = None
