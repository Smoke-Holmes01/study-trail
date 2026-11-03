"""Public responses for template and independent-content APIs."""
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field
from .outputs import ExerciseDTO, SourceDTO
from .schemas import PlanPayload

class ExerciseLinkDTO(BaseModel):
    plan_id: UUID
    stage_id: UUID | None = None
    task_id: UUID | None = None

class IndependentExerciseDTO(ExerciseDTO):
    links: list[ExerciseLinkDTO] = Field(default_factory=list)
    sources: list[SourceDTO] = Field(default_factory=list)

class ExistingAnswerDTO(BaseModel):
    exercise_id: UUID
    version: int
    available: bool
    answer_text: str | None
    explanation: str | None
    origin_kind: str
    source_ids: list[UUID] = Field(default_factory=list)
    sources: list[SourceDTO] = Field(default_factory=list)

class ConfigChoicesDTO(BaseModel):
    system_prompt: str
    model_id: str
    mcp_server_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)

class TemplateExerciseDTO(BaseModel):
    question_text: str
    provenance: str = "generated"
    source_ids: list[UUID] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    answer_text: str | None = None
    explanation: str | None = None

class TemplateSummaryDTO(BaseModel):
    id: UUID
    name: str
    description: str
    published_version_id: UUID | None = None
    version_id: UUID | None = None

class TemplatePreviewDTO(TemplateSummaryDTO):
    config_choices: ConfigChoicesDTO
    plan: PlanPayload
    exercises: list[TemplateExerciseDTO]
    asset_manifest: list[dict[str, Any]]
    capability_status: dict[str, Any]

class TemplateVersionDTO(BaseModel):
    id: UUID
    version_no: int
    status: str
    lock_version: int
    payload: dict[str, Any]
    template_id: UUID | None = None
    created_at: datetime | None = None

class AdminTemplateDTO(TemplateSummaryDTO):
    status: str
    lock_version: int
    versions: list[TemplateVersionDTO]
    created_at: datetime
    updated_at: datetime

class AdoptionDTO(BaseModel):
    id: UUID
    agent_id: UUID
    plan_ids: list[UUID]
    exercise_ids: list[UUID]
    template_version_id: UUID
    import_batch_id: UUID | None
    import_status: str

class ImportItemDTO(BaseModel):
    id: UUID
    file_id: UUID | None
    filename: str | None = None
    status: str
    error_code: str | None
    can_retry: bool

class ImportBatchDTO(BaseModel):
    id: UUID
    status: str
    revision: int
    items: list[ImportItemDTO]

class TemplateAssetDTO(BaseModel):
    id: UUID
    template_id: UUID
    filename: str
    media_type: str
    byte_size: int
    status: str
    parse_version: int
    latest_task_id: UUID | None
    error_code: str | None
    reference_key: str
    locator: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

class AssetTaskDTO(BaseModel):
    asset: TemplateAssetDTO
    task: dict[str, Any]

class AssetUploadDTO(BaseModel):
    items: list[dict[str, Any]]

class AssetPreviewDTO(BaseModel):
    id: UUID
    name: str
    reference_key: str
    blocks: list[dict[str, Any]]
    next_cursor: int | None
    warning: str
