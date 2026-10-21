"""Transactional platform configuration. Files are import sources, never runtime fallbacks."""

import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from fastapi.encoders import jsonable_encoder

from . import db
from .config import settings
from .core import Problem, require
from .skills import DEFAULT_SKILL_IDS

PURPOSES = (
    "intent",
    "coverage",
    "answer",
    "plan_extract",
    "plan_format_repair",
    "prompt_generate",
    "prompt_polish",
    "context_summary",
)
DEFAULT_LIMITS = dict(
    file_max_bytes=52428800,
    batch_max_files=10,
    image_max_bytes=10485760,
    message_max_images=6,
    ai_timeout_seconds=300,
    file_timeout_seconds=600,
    tool_rounds=3,
    tool_slots=6,
    tool_result_max_bytes=65536,
    tool_timeout_seconds=30,
)
BOUNDS = {k: (1, v) for k, v in DEFAULT_LIMITS.items()}
BOUNDS.update(
    ai_timeout_seconds=(1, 3600), file_timeout_seconds=(1, 3600), tool_result_max_bytes=(16384, 65536)
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            jsonable_encoder(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def invalid(message="平台配置参数不合法", field=None):
    details = {"field_errors": [{"field": field, "message": message}]} if field else None
    raise Problem("VALIDATION_ERROR", message, 422, details)


def lock(conn):
    # Shared order for acceptance, edits, switches and file processing.
    conn.execute(
        sa.select(db.platform_bindings).order_by(db.platform_bindings.c.slot).with_for_update()
    ).all()
    conn.execute(
        sa.select(db.platform_configs)
        .order_by(db.platform_configs.c.kind, db.platform_configs.c.stable_id)
        .with_for_update()
    ).all()
    # Also serialize the first insert before any bindings exist.
    conn.execute(sa.text("SELECT pg_advisory_xact_lock(839120410)"))


def cipher(key_id=None):
    cfg = settings()
    key_id = key_id or cfg.platform_key_id
    key = cfg.platform_keys.get(key_id)
    if not key:
        raise Problem("CONFIG_INVALID", "平台加密密钥未配置", 503)
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise Problem("CONFIG_INVALID", "平台加密密钥不可用", 503) from None


def decrypt(ident):
    with db.engine.connect() as conn:
        row = require(db.one(conn, db.credential_versions, UUID(ident)))
        try:
            return cipher(row["key_id"]).decrypt(row["ciphertext"]).decode()
        except (InvalidToken, UnicodeError):
            raise Problem("CONFIG_INVALID", "平台凭据不可解密", 503) from None


def credential(conn, config_id, slot, value, actor):
    if (
        not isinstance(value, str)
        or len(value.encode()) > 65536
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", slot)
    ):
        invalid("凭据槽或长度不合法")
    version = (
        conn.scalar(
            sa.select(sa.func.coalesce(sa.func.max(db.credential_versions.c.version_no), 0)).where(
                db.credential_versions.c.config_id == config_id, db.credential_versions.c.slot == slot
            )
        )
        + 1
    )
    ident = uuid4()
    conn.execute(
        db.credential_versions.insert().values(
            id=ident,
            config_id=config_id,
            slot=slot,
            version_no=version,
            ciphertext=cipher().encrypt(value.encode()),
            key_id=settings().platform_key_id,
            created_by=actor,
        )
    )
    return str(ident)


def validate(kind, payload, purpose=None, stable_id=None):
    if not isinstance(payload, dict):
        invalid()
    payload = dict(payload)
    if kind in {"model", "mcp"} and "{env:" in json.dumps(payload):
        invalid("运行配置不能引用环境凭据，请通过加密凭据槽写入")
    for field in ("exposed", "default_enabled"):
        if field in payload and type(payload[field]) is not bool:
            invalid("开关字段必须为布尔值")
    allowed = {
        "model": {
            "model_kind",
            "api_model_id",
            "endpoint",
            "capabilities",
            "model_revision",
            "dimensions",
            "normalization_version",
            "chunking_version",
            "exposed",
        },
        "prompt": {"purpose", "body"},
        "skill": {"body", "description", "content_version", "default_enabled"},
        "mcp": {
            "transport",
            "command",
            "args",
            "cwd",
            "env",
            "url",
            "nonsecret_headers",
            "headers",
            "allowed_tools",
            "timeout_seconds",
            "default_enabled",
            "parameter_schemas",
        },
        "limits": set(DEFAULT_LIMITS),
    }
    if kind not in allowed or set(payload) - allowed[kind]:
        invalid("配置包含未知字段")
    if kind == "limits":
        if set(payload) != set(DEFAULT_LIMITS):
            invalid("系统限制字段不完整")
        for key, (lo, hi) in BOUNDS.items():
            if type(payload[key]) is not int or not lo <= payload[key] <= hi:
                invalid(f"{key} 应为 {lo}—{hi} 的整数")
    elif kind in {"prompt", "skill"}:
        if kind == "skill" and stable_id is not None:
            expected_default = stable_id in DEFAULT_SKILL_IDS
            if "default_enabled" in payload and payload["default_enabled"] != expected_default:
                invalid("技能默认选择由三项内置技能规则确定，不可修改", "payload.default_enabled")
            payload["default_enabled"] = expected_default
        body = payload.get("body")
        if (
            not isinstance(body, str)
            or not body.strip()
            or len(json.dumps(payload, ensure_ascii=False).encode()) > 65536
        ):
            invalid("正文不能为空，且不得超过64 KiB")
        if kind == "prompt" and (purpose not in PURPOSES or payload.get("purpose") != purpose):
            invalid("提示词用途不合法或与目录不一致")
    elif kind == "model":
        model_kind = payload.get("model_kind")
        if (
            model_kind not in {"generative", "embedding", "rerank"}
            or not isinstance(payload.get("api_model_id"), str)
            or not payload["api_model_id"].strip()
        ):
            invalid("模型用途及接口模型标识不能为空")
        endpoint = urlsplit(str(payload.get("endpoint", "")))
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            invalid("模型连接地址不合法，不得含凭据或查询参数")
        caps = payload.get("capabilities", {})
        if not isinstance(caps, dict) or set(caps) - {
            "supports_images",
            "input_token_budget",
            "max_output_tokens",
            "max_images",
            "max_image_bytes",
            "json_object",
            "verified_at",
            "budget_basis",
        }:
            invalid("模型能力字段不合法")
        if model_kind == "embedding" and (
            type(payload.get("dimensions")) is not int or payload["dimensions"] != 1024
        ):
            invalid("首版仅支持1024维嵌入")
        if model_kind == "generative":
            for key in ("input_token_budget", "max_output_tokens"):
                if type(caps.get(key)) is not int or caps[key] <= 0:
                    invalid("上下文和输出预算必须是正整数")
            for key in ("max_images", "max_image_bytes"):
                if type(caps.get(key)) is not int or caps[key] < 0:
                    invalid("图片能力必须为非负整数")
            if type(caps.get("supports_images")) is not bool or (
                not caps["supports_images"] and (caps["max_images"] or caps["max_image_bytes"])
            ):
                invalid("图片开关与容量不一致")
    elif kind == "mcp":
        transport = payload.get("transport")
        tools = payload.get("allowed_tools")
        if (
            not isinstance(tools, list)
            or not tools
            or any(not isinstance(t, str) or not t for t in tools)
            or len(set(tools)) != len(tools)
        ):
            invalid("工具白名单须为非空且不重复的名称数组")
        timeout = payload.get("timeout_seconds", 30)
        if type(timeout) is not int or not 1 <= timeout <= 30:
            invalid("工具超时应为1—30秒")
        if transport == "stdio":
            if payload.get("url") or payload.get("headers") or payload.get("nonsecret_headers"):
                invalid("stdio与HTTP字段不可混用")
            if payload.get("command") not in settings().stdio_allowed_commands:
                invalid("程序不在部署允许列表内")
            args = payload.get("args", [])
            if (
                not isinstance(args, list)
                or len(args) > 100
                or any(not isinstance(arg, str) or len(arg) > 4096 for arg in args)
            ):
                invalid("程序参数必须是有长度限制的字符串数组")
            from pathlib import Path

            cwd = payload.get("cwd")
            if cwd and str(Path(cwd).resolve()) not in settings().stdio_allowed_directories:
                invalid("工作目录不在部署允许列表内")
        elif transport == "streamable_http":
            url = urlsplit(str(payload.get("url", "")))
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                # Imported Exa's non-secret tools selector is permitted.
                if not (
                    url.scheme in {"http", "https"}
                    and url.hostname == "mcp.exa.ai"
                    and url.query == "tools=web_search_exa"
                    and not url.username
                    and not url.password
                    and not url.fragment
                ):
                    invalid("MCP连接地址不合法")
            if payload.get("command") or payload.get("args") or payload.get("cwd") or payload.get("env"):
                invalid("HTTP与stdio字段不可混用")
        else:
            invalid("不支持的传输方式")
        from jsonschema import Draft202012Validator

        constraints = payload.get("parameter_schemas", {})
        if not isinstance(constraints, dict):
            invalid("工具参数约束必须为JSON对象", "payload.parameter_schemas")
        for name, schema in constraints.items():
            if name not in tools:
                invalid("参数约束只能属于白名单工具", "payload.parameter_schemas")
            try:
                Draft202012Validator.check_schema(schema)
            except Exception:
                invalid("工具参数Schema不合法", "payload.parameter_schemas")
        for mapping in (
            payload.get("env", {}),
            payload.get("headers", {}),
            payload.get("nonsecret_headers", {}),
        ):
            if not isinstance(mapping, dict) or any(not isinstance(v, str) for v in mapping.values()):
                invalid("连接字段应为字符串映射")
            if any(
                any(x in k.lower() for x in ("authorization", "token", "secret", "password", "key"))
                for k in mapping
            ):
                invalid("秘密字段必须通过凭据槽写入")
    return payload


def fingerprint(payload):
    return digest(
        {
            "endpoint_identity": payload["endpoint"].rstrip("/"),
            "api_model_id": payload["api_model_id"],
            "model_revision": payload.get("model_revision", "initial"),
            "dimensions": payload["dimensions"],
            "normalization_version": payload.get("normalization_version", "dense-v1"),
            "chunking_version": payload.get("chunking_version", "v1"),
        }
    )


def embedding_guard(conn, new_fingerprint):
    old = (
        conn.execute(sa.select(db.platform_bindings).where(db.platform_bindings.c.slot == "embedding"))
        .mappings()
        .first()
    )
    if not old or old["index_fingerprint"] == new_fingerprint:
        return
    indexed = conn.scalar(sa.select(sa.func.count()).select_from(db.document_chunks))
    processing = conn.scalar(
        sa.select(sa.func.count())
        .select_from(db.tasks)
        .where(db.tasks.c.kind == "file_process", db.tasks.c.status.in_(["queued", "running"]))
    )
    if indexed or processing:
        raise Problem("EMBEDDING_INDEX_INCOMPATIBLE", "已有索引或在途文件任务，不能切换不兼容嵌入配置", 409)


def revision(conn, config, payload, secret_updates, actor):
    old = db.one(conn, db.config_revisions, config["current_revision_id"])
    refs = dict(old["credential_version_ids"]) if old else {}
    if secret_updates is not None and not isinstance(secret_updates, dict):
        invalid("凭据更新必须是映射")
    for slot, value in (secret_updates or {}).items():
        if value is None or isinstance(value, str) and not value.strip():
            continue
        if config["kind"] not in {"model", "mcp"}:
            invalid("此配置不接受凭据")
        if config["kind"] == "model" and slot != "api_key":
            invalid("模型仅接受api_key凭据槽")
        refs[slot] = credential(conn, config["id"], slot, value, actor)
    if config["kind"] == "model" and not refs.get("api_key"):
        invalid("请提供模型接口凭据")
    ident = config["current_revision_id"] if not old else uuid4()
    payload = validate(config["kind"], payload, config["purpose"], config["stable_id"])
    number = old["revision_no"] + 1 if old else 1
    conn.execute(
        db.config_revisions.insert().values(
            id=ident,
            config_id=config["id"],
            revision_no=number,
            payload=payload,
            credential_version_ids=refs,
            content_hash=digest([payload, refs]),
            created_by=actor,
        )
    )
    conn.execute(
        db.platform_configs.update()
        .where(db.platform_configs.c.id == config["id"])
        .values(
            current_revision_id=ident,
            updated_at=db.now(),
            lock_version=config["lock_version"] + (1 if old else 0),
        )
    )
    bindings = (
        conn.execute(sa.select(db.platform_bindings).where(db.platform_bindings.c.config_id == config["id"]))
        .mappings()
        .all()
    )
    for binding in bindings:
        if binding["slot"] == "default_generation" and not payload.get("exposed", True):
            raise Problem("REQUIRED_CONFIG_MISSING", "默认生成模型必须向用户开放，请先切换默认模型", 409)
        fp = fingerprint(payload) if binding["slot"] == "embedding" else None
        if fp:
            embedding_guard(conn, fp)
        conn.execute(
            db.platform_bindings.update()
            .where(db.platform_bindings.c.slot == binding["slot"])
            .values(
                revision_id=ident,
                lock_version=binding["lock_version"] + 1,
                updated_at=db.now(),
                index_fingerprint=fp,
            )
        )
    return db.one(conn, db.config_revisions, ident)


def create(conn, kind, body, actor):
    allowed = {"stable_id", "name", "description", "enabled", "purpose", "payload", "secret_updates"}
    if set(body) - allowed:
        invalid("目录包含未知字段")
    stable = body.get("stable_id", "")
    pattern = r"[a-z0-9]+(?:-[a-z0-9]+)*" if kind in {"skill", "mcp"} else r"[A-Za-z0-9][A-Za-z0-9._-]*"
    if (
        not isinstance(stable, str)
        or not re.fullmatch(pattern, stable)
        or len(stable) > (64 if kind in {"skill", "mcp"} else 80)
    ):
        invalid("稳定标识不合法")
    if conn.execute(
        sa.select(db.platform_configs.c.id).where(
            db.platform_configs.c.kind == kind, db.platform_configs.c.stable_id == stable
        )
    ).first():
        raise Problem("CONFIG_EXISTS", "稳定标识已存在", 409)
    name, description = body.get("name", ""), body.get("description", "")
    if (
        not isinstance(name, str)
        or not 1 <= len(name.strip()) <= 80
        or not isinstance(description, str)
        or len(description) > 500
    ):
        invalid("名称或说明长度不合法")
    if type(body.get("enabled", False)) is not bool:
        invalid("启用状态必须是布尔值")
    if kind == "prompt" and body.get("enabled"):
        invalid("新提示词须先保存，再显式启用切换")
    config = dict(
        id=uuid4(),
        kind=kind,
        stable_id=stable,
        name=name.strip(),
        description=description,
        enabled=body.get("enabled", False),
        purpose=body.get("purpose"),
        current_revision_id=uuid4(),
        lock_version=1,
    )
    conn.execute(db.platform_configs.insert().values(**config))
    revision(conn, config, body.get("payload"), body.get("secret_updates"), actor)
    return db.one(conn, db.platform_configs, config["id"])


def bound(conn, slot):
    binding = (
        conn.execute(sa.select(db.platform_bindings).where(db.platform_bindings.c.slot == slot))
        .mappings()
        .first()
    )
    if not binding:
        message = {
            "default_generation": "平台尚未配置默认生成模型，请联系管理员。",
            "embedding": "平台尚未配置嵌入模型，知识库索引暂不可用。",
            "rerank": "平台尚未配置重排模型，知识库检索暂不可用。",
        }.get(slot, "必要平台配置未初始化")
        raise Problem("REQUIRED_CONFIG_MISSING", message, 409)
    config = require(db.one(conn, db.platform_configs, binding["config_id"]))
    if not config["enabled"] or config["deleted_at"] is not None:
        raise Problem("CONFIG_UNAVAILABLE", "必要平台配置已停用", 409)
    return config, require(db.one(conn, db.config_revisions, binding["revision_id"]))


def projection(conn, config):
    rev = require(db.one(conn, db.config_revisions, config["current_revision_id"]))
    return {
        **{
            key: config[key]
            for key in (
                "id",
                "kind",
                "stable_id",
                "name",
                "description",
                "enabled",
                "purpose",
                "current_revision_id",
                "lock_version",
                "updated_at",
            )
        },
        "revision_no": rev["revision_no"],
        "payload": rev["payload"],
        "secret_slots": {key: {"has_value": True} for key in rev["credential_version_ids"]},
    }


def runtime(config, rev):
    p = rev["payload"]
    refs = {k: "credential:" + v for k, v in rev["credential_version_ids"].items()}
    common = dict(config_id=str(config["id"]), revision_id=str(rev["id"]))
    if config["kind"] == "model":
        return {
            **common,
            **p.get("capabilities", {}),
            "model_id": config["stable_id"],
            "api_model_id": p["api_model_id"],
            "display_name": config["name"],
            "enabled": config["enabled"],
            "provider": {"base_url": p["endpoint"], "api_key": refs.get("api_key", "")},
            "index_fingerprint": fingerprint(p) if p["model_kind"] == "embedding" else None,
        }
    if config["kind"] == "mcp":
        headers = dict(p.get("nonsecret_headers", p.get("headers", {})))
        env = dict(p.get("env", {}))
        for slot, ref in refs.items():
            if slot.startswith("env."):
                env[slot[4:]] = ref
            else:
                name = slot.removeprefix("header.")
                headers[name] = ref
        return {
            **common,
            **p,
            "headers": headers,
            "env": env,
            "name": config["name"],
            "description": config["description"],
            "enabled": config["enabled"],
            "args": p.get("args", []),
            "default_enabled": p.get("default_enabled", False),
            "timeout_seconds": p.get("timeout_seconds", 30),
        }
    return {
        **common,
        **p,
        "id": config["stable_id"],
        "name": config["name"],
        "description": config["description"],
        "version": str(rev["revision_no"]),
        "default_enabled": config["enabled"] and config["stable_id"] in DEFAULT_SKILL_IDS,
    }


def catalog(kind, *, include_disabled=False):
    with db.engine.connect() as conn:
        result = {}
        for config in conn.execute(
            sa.select(db.platform_configs).where(db.platform_configs.c.kind == kind, db.platform_configs.c.deleted_at.is_(None))
        ).mappings():
            rev = require(db.one(conn, db.config_revisions, config["current_revision_id"]))
            if not include_disabled and not config["enabled"]:
                continue
            if kind == "model" and (
                rev["payload"]["model_kind"] != "generative" or not rev["payload"].get("exposed", True)
            ):
                continue
            result[config["stable_id"]] = runtime(config, rev)
        return result


def limits(conn=None):
    if conn is None:
        with db.engine.connect() as connection:
            return limits(connection)
    return dict(bound(conn, "limits")[1]["payload"])


def model_readiness(conn):
    result = {}
    for slot in ("default_generation", "embedding", "rerank"):
        try:
            bound(conn, slot)
            result[slot] = True
        except Problem as exc:
            if exc.code not in {"REQUIRED_CONFIG_MISSING", "CONFIG_UNAVAILABLE"}:
                raise
            result[slot] = False
    return result


def snapshot(conn, model_id=None, *, generation_required=True):
    lock(conn)
    if model_id:
        default = (
            conn.execute(
                sa.select(db.platform_configs).where(
                    db.platform_configs.c.kind == "model",
                    db.platform_configs.c.stable_id == model_id,
                    db.platform_configs.c.enabled.is_(True),
                    db.platform_configs.c.deleted_at.is_(None),
                )
            )
            .mappings()
            .first()
        )
        if default is None:
            raise Problem("MODEL_UNAVAILABLE", "当前模型已停用或不可用，请手动选择模型", 503)
        default_rev = require(db.one(conn, db.config_revisions, default["current_revision_id"]))
        if default_rev["payload"]["model_kind"] != "generative" or not default_rev["payload"].get(
            "exposed", True
        ):
            raise Problem("MODEL_UNAVAILABLE", "当前模型未开放", 503)
        result = runtime(default, default_rev)
    elif generation_required:
        result = runtime(*bound(conn, "default_generation"))
    else:
        result = {}
    readiness = model_readiness(conn)
    for slot in ("embedding", "rerank"):
        result[slot] = runtime(*bound(conn, slot)) if readiness[slot] else None
    result["limits"] = limits(conn)
    result["prompts"] = {p: bound(conn, "prompt:" + p)[1]["payload"]["body"] for p in PURPOSES}
    from .builtin_search import configuration
    result["builtin_search"] = configuration()
    return result


def bind(conn, slot, config, *, expected=None):
    current = (
        conn.execute(sa.select(db.platform_bindings).where(db.platform_bindings.c.slot == slot))
        .mappings()
        .first()
    )
    if expected is not None and (current["lock_version"] if current else 0) != expected:
        raise Problem("CONFIG_VERSION_CONFLICT", "生效配置已更新，请重新载入", 409)
    rev = require(db.one(conn, db.config_revisions, config["current_revision_id"]))
    payload = rev["payload"]
    if not config["enabled"] or config["deleted_at"] is not None:
        raise Problem("CONFIG_UNAVAILABLE", "所选配置尚未启用", 409)
    if slot in {"default_generation", "embedding", "rerank"}:
        if config["kind"] != "model" or payload["model_kind"] != {"default_generation": "generative"}.get(
            slot, slot
        ):
            invalid("模型用途与生效槽不一致")
        if slot == "default_generation" and not payload.get("exposed", True):
            invalid("默认生成模型必须向用户开放")
    elif slot.startswith("prompt:"):
        if config["kind"] != "prompt" or slot != "prompt:" + str(config["purpose"]):
            invalid("提示词用途不一致")
    elif slot != "limits" or config["kind"] != "limits":
        invalid("未知生效槽")
    fp = fingerprint(payload) if slot == "embedding" else None
    if fp:
        embedding_guard(conn, fp)
    values = dict(
        config_id=config["id"],
        revision_id=rev["id"],
        index_fingerprint=fp,
        lock_version=current["lock_version"] + 1 if current else 1,
        updated_at=db.now(),
    )
    if current:
        conn.execute(
            db.platform_bindings.update().where(db.platform_bindings.c.slot == slot).values(**values)
        )
    else:
        conn.execute(db.platform_bindings.insert().values(slot=slot, **values))
