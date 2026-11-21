"""Domain model: Incident and its vocabularies (see GLOSSARY.md)."""

from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)


class Environment(StrEnum):
    DEV = "dev"
    QA = "qa"
    STAGE = "stage"
    PROD = "prod"


class Service(StrEnum):
    PAYROLL = "payroll"
    TIMEKEEPING = "timekeeping"
    AUTH = "auth"
    BENEFITS = "benefits"
    SCHEDULING = "scheduling"
    REPORTING = "reporting"
    NOTIFICATIONS = "notifications"
    INTEGRATIONS = "integrations"


class Severity(StrEnum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


IncidentId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


def _normalise_tag(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


# Normalise before the pattern check; StringConstraints checks the pattern on raw input.
Tag = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$"),
    BeforeValidator(_normalise_tag),
]


def _dedupe(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(tags))


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: IncidentId
    created_at: AwareDatetime
    environment: Environment
    service: Service
    severity: Severity
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    resolution_summary: str = Field(min_length=1, max_length=5000)
    tags: Annotated[list[Tag], Field(max_length=20), AfterValidator(_dedupe)] = []


class IngestResult(BaseModel):
    ingested: int = 0
    updated: int = 0
    skipped: int = 0
