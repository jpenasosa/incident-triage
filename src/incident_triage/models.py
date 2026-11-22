"""Domain model: Incident and its vocabularies (see GLOSSARY.md)."""

from datetime import UTC, datetime
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


# Caller-supplied, so ingest can match on it. Stored as SQLite's INTEGER PRIMARY KEY.
MAX_INCIDENT_ID = 2**63 - 1  # SQLite's largest integer


class CustomerStatus(StrEnum):
    """Customer-safe incident status (GLOSSARY.md)."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


class ImpactCategory(StrEnum):
    """Customer-safe description of what a problem affects (GLOSSARY.md)."""

    LOGIN_ACCESS = "login_access"
    PAY_CALCULATION = "pay_calculation"
    TIME_TRACKING = "time_tracking"
    DATA_DELAY = "data_delay"
    NOTIFICATION_DELIVERY = "notification_delivery"
    REPORT_GENERATION = "report_generation"
    INTEGRATION_SYNC = "integration_sync"
    PERFORMANCE = "performance"
    OTHER = "other"


IncidentId = Annotated[int, Field(strict=True, gt=0, le=MAX_INCIDENT_ID)]


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


def _to_utc(value: datetime) -> datetime:
    # Stored as ISO text; one offset keeps lexical order == chronological order.
    return value.astimezone(UTC)


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: IncidentId
    created_at: Annotated[AwareDatetime, AfterValidator(_to_utc)]
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


MAX_SEARCH_LIMIT = 50


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


class SearchQuery(BaseModel):
    """Search parameters. Used directly as the /search query-parameter model."""

    model_config = ConfigDict(extra="forbid")

    q: Annotated[str | None, BeforeValidator(_blank_to_none)] = Field(default=None, max_length=500)
    environment: Environment | None = None
    service: Service | None = None
    severity: Severity | None = None
    tags: list[Tag] = Field(default=[], max_length=20)
    limit: int = Field(default=10, ge=1, le=MAX_SEARCH_LIMIT)
    offset: int = Field(default=0, ge=0)

    @property
    def has_filters(self) -> bool:
        return bool(self.environment or self.service or self.severity or self.tags)


class SearchResult(BaseModel):
    id: int
    title: str
    snippet: str
    score: float | None  # higher is more relevant; None for filter-only searches
    service: Service
    environment: Environment
    severity: Severity
    created_at: datetime


class SearchPage(BaseModel):
    total: int
    limit: int
    offset: int
    results: list[SearchResult]


# Stripped before the length checks, so a whitespace-only question is a 422 rather than a
# silent filter-only search (SearchQuery treats a blank q as absent).
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class AssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question
    environment: Environment | None = None
    service: Service | None = None
    severity: Severity | None = None
    tags: list[Tag] = Field(default=[], max_length=20)


class CitedIncident(BaseModel):
    id: int
    title: str
    reason: str


class AssistResponse(BaseModel):
    relevant_incidents: list[CitedIncident]
    next_steps: list[str]
    customer_draft: str
    insufficient_context: bool
