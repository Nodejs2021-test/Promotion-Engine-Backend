"""Programme eligibility request model (Item Scope Spec §6)."""

from datetime import date

from pydantic import BaseModel, Field, model_validator

from app.modules.pricing.model import ELIGIBILITY_MODES, ELIGIBILITY_SCOPES, PROGRAMMES


class EligibilityIn(BaseModel):
    programme_code: str
    scope: str = Field(description="CUSTOMER, CUSTOMER_GROUP, BANNER or CHANNEL")
    value: str = Field(min_length=1, max_length=120, description="The customer ID, group, banner or channel")
    eligibility_mode: str = "ELIGIBLE"
    valid_from: date
    valid_to: date | None = None
    active: bool = True
    source_reference: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _check(self):
        if self.programme_code not in PROGRAMMES:
            raise ValueError(f"Unknown programme '{self.programme_code}'")
        if self.scope not in ELIGIBILITY_SCOPES:
            raise ValueError(f"Unknown scope '{self.scope}'")
        if self.eligibility_mode not in ELIGIBILITY_MODES:
            raise ValueError(f"Unknown eligibility mode '{self.eligibility_mode}'")
        if self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("Valid to must not precede Valid from")
        self.value = self.value.strip()
        return self
