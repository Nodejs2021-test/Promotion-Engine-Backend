"""User administration request models."""

from pydantic import BaseModel, Field

from app.modules.roles.schema import Role


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.\-]+$")
    full_name: str = Field(min_length=1, max_length=120)
    email: str | None = None
    role: Role
    password: str = Field(min_length=8, max_length=128)
    active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=120)
    email: str | None = None
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(None, min_length=8, max_length=128)


class ApproveIn(BaseModel):
    role: Role
