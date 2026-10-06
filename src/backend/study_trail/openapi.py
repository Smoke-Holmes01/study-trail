from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema

from . import outputs as o


def install(app):
    simple = {
        "AUTH-01": o.StudentDTO,
        "AUTH-02": o.LoginDTO,
        "AUTH-03": o.StudentDTO,
        "AUTH-04": o.CsrfDTO,
        "AUTH-06": o.StudentDTO,
        "M-01": o.ModelsDTO,
        "A-02": o.AgentDTO,
        "A-03": o.AgentDTO,
        "A-04": o.AgentDTO,
        "A-07": o.TaskAcceptedDTO,
        "C-02": o.ConversationDTO,
        "C-03": o.ConversationDTO,
        "C-04": o.ConversationDTO,
        "C-06": o.ConversationDTO,
        "C-09": o.ChatAcceptedDTO,
        "C-10": o.ChatAcceptedDTO,
        "I-01": o.AttachmentDTO,
        "P-02": o.PlanDTO,
        "P-03": o.PlanDTO,
        "K-02": o.KnowledgeBaseDTO,
        "K-03": o.KnowledgeBaseDTO,
        "K-04": o.KnowledgeBaseDTO,
        "F-02": o.UploadDTO,
        "F-03": o.FileDTO,
        "F-04": o.PreviewDTO,
        "F-05": o.FileAcceptedDTO,
        "Q-01": o.ExerciseDTO,
        "Q-02": o.ChatAcceptedDTO,
        "S-01": o.SourceDTO,
        "S-02": o.PreviewDTO,
        "T-01": o.TaskDTO,
        "T-03": o.TaskDTO,
        "T-04": o.TaskAcceptedDTO,
    }
    for key, value in {
        "A-01": o.AgentDTO,
        "C-01": o.ConversationDTO,
        "C-08": o.MessageDTO,
        "P-01": o.PlanDTO,
        "K-01": o.KnowledgeBaseDTO,
        "F-01": o.FileDTO,
    }.items():
        simple[key] = o.Page[value]
    for key in ["A-06", "C-07", "P-05", "K-06", "F-07"]:
        simple[key] = o.ImpactDTO
    for key in ["A-05", "C-05", "I-03", "P-04", "K-05", "F-06"]:
        simple[key] = o.DeletedDTO
    created = {"AUTH-01", "A-02", "C-02", "I-01", "K-02"}
    accepted = {"A-07", "C-09", "C-10", "F-02", "F-05", "Q-02", "T-04"}

    def document():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        models = [o.Success[v] for v in simple.values()] + [o.ErrorEnvelope]
        refs, definitions = models_json_schema(
            [(m, "serialization") for m in set(models)], ref_template="#/components/schemas/{model}"
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(definitions["$defs"])
        import re

        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                ident = operation.get("operationId")
                parameters = operation.setdefault("parameters", [])
                for name in re.findall(r"\{([^}]+)\}", path):
                    if not any(p.get("name") == name for p in parameters):
                        parameters.append(
                            {
                                "name": name,
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string", "format": "uuid"},
                            }
                        )
                if ident in simple:
                    status = "201" if ident in created else "202" if ident in accepted else "200"
                    operation["responses"].pop("200", None)
                    operation["responses"][status] = {
                        "description": "成功",
                        "content": {
                            "application/json": {"schema": refs[(o.Success[simple[ident]], "serialization")]}
                        },
                    }
                    if ident == "F-02":
                        operation["responses"]["200"] = {
                            "description": "全部文件逐项拒绝，无任务受理",
                            "content": {
                                "application/json": {
                                    "schema": refs[(o.Success[o.UploadDTO], "serialization")]
                                }
                            },
                        }
                for status in ["401", "403", "404", "409", "410", "422", "503"]:
                    operation["responses"][status] = {
                        "description": "业务错误",
                        "content": {"application/json": {"schema": refs[(o.ErrorEnvelope, "serialization")]}},
                    }
                if ident == "I-02":
                    operation["responses"]["200"] = {
                        "description": "私有图片",
                        "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
                    }
                if ident == "T-02":
                    operation["responses"]["200"] = {
                        "description": "完整任务快照 SSE",
                        "content": {"text/event-stream": {"schema": {"type": "string"}}},
                    }
        app.openapi_schema = schema
        return schema

    app.openapi = document
