from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_NAME_EXPERIMENT_PLAN = "lspr_experiment_plan"
SCHEMA_NAME_FLOW_PLAN = SCHEMA_NAME_EXPERIMENT_PLAN
SCHEMA_NAME_SESSION = "lspr_session"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SchemaInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Stable schema name.")
    version: int = Field(..., ge=1, description="Schema version number.")


class SuiteIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    app_name: str = Field(..., description="User-facing application name.")
    app_version: str = Field(..., description="Application version string.")
    format_name: str = Field(..., description="Persisted file format name.")
    format_version: int = Field(..., ge=1, description="Persisted file format version.")
    created_at_utc: str = Field(default_factory=utc_now_iso)


class ExperimentPlanStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(..., ge=0)
    label: str | None = None
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., ge=0)
    color: str | None = None
    comment: str | None = None
    devices: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def with_timing(self, start_s: float, end_s: float) -> "ExperimentPlanStep":
        return self.model_copy(update={"start_s": start_s, "end_s": end_s})


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_info: SchemaInfo = Field(
        default_factory=lambda: SchemaInfo(name=SCHEMA_NAME_EXPERIMENT_PLAN, version=1),
        alias="schema",
    )
    identity: SuiteIdentity | None = None
    units: dict[str, str] = Field(default_factory=dict)
    steps: list[ExperimentPlanStep] = Field(default_factory=list)

    def step_by_id(self, step_id: int) -> ExperimentPlanStep | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def ordered(self) -> list[ExperimentPlanStep]:
        return sorted(self.steps, key=lambda step: (step.start_s, step.id))
