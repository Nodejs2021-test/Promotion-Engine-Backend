"""Business settings request models."""

from pydantic import BaseModel, Field, field_validator


class AppSettingsIn(BaseModel):
    organization_name: str = Field(min_length=1, max_length=120)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$", description="ISO 4217 code, e.g. USD")
    timezone: str = Field(min_length=1, description="IANA timezone, e.g. Asia/Kolkata")


class ControlledValues(BaseModel):
    """Controlled values a Sales Order and a rule may use (Item Scope Spec §11). Empty = not controlled."""

    channel: list[str] = []
    banner: list[str] = []
    marketing_flag: list[str] = []

    @field_validator("channel", "banner", "marketing_flag", mode="before")
    @classmethod
    def _clean(cls, v):
        if isinstance(v, str):
            v = v.split(",")
        out: list[str] = []
        for x in v or []:
            s = str(x).strip()
            if s and s.casefold() not in {o.casefold() for o in out}:
                out.append(s)
        return out


class AppSettingsUpdate(BaseModel):
    organization_name: str | None = Field(None, min_length=1, max_length=120)
    currency: str | None = Field(None, pattern=r"^[A-Za-z]{3}$")
    timezone: str | None = None
    controlled_values: ControlledValues | None = None
