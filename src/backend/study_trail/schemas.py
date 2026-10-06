from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Register(Input):
    login: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str

    @field_validator("login", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def match(self):
        if self.password != self.confirm_password:
            raise ValueError("两次密码不一致")
        return self


class Login(Input):
    login: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("login", mode="before")
    @classmethod
    def trim_login(cls, value):
        return value.strip() if isinstance(value, str) else value


class Password(Input):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str

    @model_validator(mode="after")
    def match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("两次密码不一致")
        return self


class Name(Input):
    name: str = Field(min_length=1, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class ConversationCreate(Name):
    name: str = "新对话"


class AgentCreate(Name):
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(min_length=1, max_length=10000)
    knowledge_base_ids: list[UUID] = Field(default_factory=list)

    @field_validator("system_prompt", mode="before")
    @classmethod
    def trim_prompt(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("knowledge_base_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("关联知识库不能重复")
        return value


class AgentPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=10000)
    knowledge_base_ids: list[UUID] | None = None
    model_id: str | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("至少提供一个非空修改字段")
        for key in ("name", "system_prompt"):
            value = getattr(self, key)
            if value is not None:
                value = value.strip()
                if not value:
                    raise ValueError("名称和提示词不能为空白")
                setattr(self, key, value)
        if self.knowledge_base_ids is not None and len(self.knowledge_base_ids) != len(
            set(self.knowledge_base_ids)
        ):
            raise ValueError("关联知识库不能重复")
        return self


class MePatch(Input):
    display_name: str | None = Field(default=None, min_length=1, max_length=32)
    theme: Literal["light", "dark"] | None = None

    @model_validator(mode="after")
    def patch(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("至少提供一个非空修改字段")
        if self.display_name is not None:
            self.display_name = self.display_name.strip()
            if not self.display_name:
                raise ValueError("显示名称不能为空白")
        return self


class Empty(Input):
    pass


class Send(Input):
    content_text: str = ""
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=6)
    target_plan_id: UUID | None = None
    expected_plan_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_message(self):
        if not self.content_text.strip() and not self.attachment_ids:
            raise ValueError("请输入文字或选择图片")
        if len(self.attachment_ids) != len(set(self.attachment_ids)):
            raise ValueError("图片不能重复")
        if (self.target_plan_id is None) != (self.expected_plan_version is None):
            raise ValueError("目标计划和版本必须同时提供")
        return self


class Retry(Input):
    previous_task_id: UUID
    expected_plan_version: int | None = Field(default=None, ge=1)


class Prompt(Input):
    mode: Literal["generate", "polish"]
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=500)
    input_prompt: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def validate_prompt(self):
        self.name = self.name.strip()
        if not self.name or (self.mode == "polish" and not self.input_prompt.strip()):
            raise ValueError("润色需要非空原提示词；名称不能为空")
        if self.mode == "generate" and self.input_prompt:
            raise ValueError("生成模式不接受原提示词")
        return self


class PlanTask(Input):
    id: UUID
    title: str = Field(min_length=1)
    estimated_minutes: int = Field(gt=0, strict=True)
    resource_source_ids: list[UUID]
    exercise_suggestions: list[str]


class PlanStage(Input):
    id: UUID
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    knowledge_points: list[str] = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)


class PlanPayload(Input):
    title: str = Field(min_length=1, max_length=50)
    goal: str = Field(min_length=1)
    deadline: date | None
    stages: list[PlanStage] = Field(min_length=1)

    @model_validator(mode="after")
    def semantic(self):
        values = [self.title, self.goal]
        ids = []
        for stage in self.stages:
            ids.append(stage.id)
            values += [stage.title, stage.goal, *stage.knowledge_points]
            for task in stage.tasks:
                ids.append(task.id)
                values += [task.title, *task.exercise_suggestions]
        if any(not value.strip() for value in values) or len(ids) != len(set(ids)):
            raise ValueError("计划内容不能为空白且条目标识不能重复")
        return self
