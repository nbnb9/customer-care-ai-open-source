from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CustomerId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class R0SelfServiceFeatureFlagUpdate(StrictRequest):
    enabled: bool
    allow_all_customers: bool = False
    customer_ids: list[CustomerId] = Field(default_factory=list, max_length=100)

    @field_validator("customer_ids")
    @classmethod
    def customer_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("customer_ids must not contain duplicates")
        return value

    @model_validator(mode="after")
    def enabled_canary_must_have_targets(self) -> "R0SelfServiceFeatureFlagUpdate":
        if self.enabled and not self.allow_all_customers and not self.customer_ids:
            raise ValueError(
                "enabled R0 self-service must allow all customers "
                "or name canary customers"
            )
        return self


class R0SelfServiceFeatureFlagRead(BaseModel):
    tenant_id: str
    enabled: bool
    allow_all_customers: bool
    customer_ids: list[str]
    master_enabled: bool
    effective_for_tenant: bool
