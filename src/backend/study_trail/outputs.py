"""Public wire contracts. Separate from persistence and provider capabilities."""

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel

from .schemas import PlanPayload

T = TypeVar("T")


class Success(BaseModel, Generic[T]):
    data: T
    request_id: UUID


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class ErrorDTO(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    error: ErrorDTO
    request_id: UUID


class StudentDTO(BaseModel):
    id: UUID
    login: str
    display_name: str
    theme: Literal["light", "dark"]


class ModelOptionDTO(BaseModel):
    id: str
    display_name: str
    supports_images: bool
    enabled: bool


class ModelsDTO(BaseModel):
    items: list[ModelOptionDTO]
    default_model_id: str


class SkillLabelDTO(BaseModel):
    id: str
    name: str
    description: str


class SkillDTO(SkillLabelDTO):
    default_enabled: bool


class SkillsDTO(BaseModel):
    items: list[SkillDTO]


class MCPServerDTO(BaseModel):
    id: str
    name: str
    description: str
    transport: Literal["stdio", "streamable_http"]
    enabled: bool
    default_enabled: bool
    status: Literal["unknown", "disabled", "connected", "unavailable"]
    tool_count: int | None = None


class MCPServersDTO(BaseModel):
    items: list[MCPServerDTO]


class MCPToolDTO(BaseModel):
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPToolsDTO(BaseModel):
    items: list[MCPToolDTO]


class MCPResultDTO(BaseModel):
    content: list[dict[str, Any]]
    structuredContent: dict[str, Any] | None = None
    isError: bool | None = None


class AgentDTO(BaseModel):
    id: UUID
    name: str
    description: str
    system_prompt: str
    model_id: str
    config_version: int
    mcp_server_ids: list[str]
    skill_ids: list[str]
    knowledge_base_ids: list[UUID]
    latest_conversation_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ConversationDTO(BaseModel):
    id: UUID
    agent_id: UUID
    name: str
    has_images: bool
    last_accessed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AttachmentDTO(BaseModel):
    id: UUID
    state: Literal["staged", "bound"]
    original_name: str
    media_type: str
    byte_size: int
    width: int
    height: int
    content_url: str


class SourceDTO(BaseModel):
    id: UUID
    kind: Literal["file", "web"]
    title: str
    status: Literal["available", "deleted", "external"]
    file_id: UUID | None
    url: str | None
    locator: dict[str, Any] | None
    excerpt: str
    captured_at: datetime


class ExerciseDTO(BaseModel):
    id: UUID
    message_id: UUID
    conversation_id: UUID
    position: int
    question_text: str
    provenance: Literal["original", "adapted", "generated"]
    source_ids: list[UUID]


class MessageDTO(BaseModel):
    id: UUID
    conversation_id: UUID
    sequence_no: int
    role: Literal["user", "assistant"]
    origin: str
    content_text: str
    skill: SkillLabelDTO | None
    response_status: str | None
    user_message_id: UUID | None
    task_id: UUID | None
    attachments: list[AttachmentDTO]
    sources: list[SourceDTO]
    exercises: list[ExerciseDTO]
    created_at: datetime


class KnowledgeBaseDTO(BaseModel):
    id: UUID
    name: str
    file_count: int
    ready_file_count: int
    created_at: datetime
    updated_at: datetime


class FileDTO(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    original_name: str
    extension: str
    byte_size: int
    status: Literal["processing", "ready", "failed"]
    parse_version: int
    indexed_at: datetime | None
    error: ErrorDTO | None
    latest_task_id: UUID | None
    created_at: datetime
    updated_at: datetime


class PlanDTO(BaseModel):
    id: UUID
    agent_id: UUID
    name: str
    content_version: int
    content: PlanPayload
    sources: list[SourceDTO]
    source_conversation_id: UUID | None
    source_message_id: UUID | None
    created_at: datetime
    updated_at: datetime


class TargetDTO(BaseModel):
    id: UUID
    name_snapshot: str
    expected_plan_version: int
    status: Literal["available", "deleted"]


class RequestInputDTO(BaseModel):
    content_text: str
    skill_id: str | None
    attachment_ids: list[UUID]
    answer_exercise_id: UUID | None
    target_plan: TargetDTO | None


class PlanMutationDTO(BaseModel):
    kind: Literal["created", "updated"]
    plan_id: UUID
    content_version: int
    committed: bool


class TaskResultDTO(BaseModel):
    preview_text: str
    sources: list[SourceDTO]
    exercise_ids: list[UUID]
    plan_mutation: PlanMutationDTO | None
    prompt_text: str | None = None
    file_id: UUID | None = None


class ProgressDTO(BaseModel):
    phase: str
    label: str


class TaskDTO(BaseModel):
    id: UUID
    kind: str
    action: str | None
    status: Literal["queued", "running", "succeeded", "failed", "stopped"]
    attempt_no: int
    user_message_id: UUID | None
    assistant_message_id: UUID | None
    request_input: RequestInputDTO | None
    revision: int
    cancel_requested: bool
    progress: ProgressDTO
    result: TaskResultDTO
    error: ErrorDTO | None
    timeout_seconds: int
    deadline_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class ChatAcceptedDTO(BaseModel):
    user_message: MessageDTO
    task: TaskDTO


class TaskAcceptedDTO(BaseModel):
    task: TaskDTO


class LoginDTO(BaseModel):
    student: StudentDTO
    csrf_token: str
    expires_at: datetime


class PreviewBlockDTO(BaseModel):
    id: UUID
    text: str
    locator: dict[str, Any]


class PreviewDTO(BaseModel):
    file_id: UUID
    name: str
    parse_version: int
    blocks: list[PreviewBlockDTO]
    next_cursor: str | None
    focus_locator: dict[str, Any] | None
    warning: str


class ImpactDTO(BaseModel):
    kind: str
    counts: dict[str, int]
    retained: list[str]
    warning: str


class DeletedDTO(BaseModel):
    deleted_id: UUID


class CsrfDTO(BaseModel):
    csrf_token: str


class UploadItemDTO(BaseModel):
    file: FileDTO | None = None
    task: TaskDTO | None = None
    original_name: str | None = None
    error: ErrorDTO | None = None


class UploadDTO(BaseModel):
    items: list[UploadItemDTO]


class FileAcceptedDTO(BaseModel):
    file: FileDTO
    task: TaskDTO
