"""Role request models."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# Role codes are validated against the roles collection in MongoDB.
Role = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=40)
]


class RoleIn(BaseModel):
    role: Role
    label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(None, max_length=300)
    permissions: list[str] = []


class RoleUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=300)
    permissions: list[str] | None = None
