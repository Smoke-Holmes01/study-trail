"""Deployment-owned MCP connections; no client-supplied commands, URLs or credentials."""

import asyncio
import hashlib
import json
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

import httpx2
from jsonschema import Draft202012Validator, ValidationError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams

from .config import settings
from .core import Problem

MAX_RESULT_BYTES = 65536


def descriptor(ident, server):
    return {
        "id": ident,
        "name": server["name"],
        "description": server["description"],
        "transport": server["transport"],
        "enabled": server["enabled"],
        "default_enabled": server["default_enabled"],
        "status": "unknown" if server["enabled"] else "disabled",
    }


def selected(ids, servers=None):
    servers = servers if servers is not None else settings().mcp_servers()
    result = {}
    for ident in ids:
        if ident not in servers or not servers[ident]["enabled"]:
            raise Problem("MCP_UNAVAILABLE", "选中的 MCP 服务已不可用，请调整智能体开关", 409)
        result[ident] = servers[ident]
    return result


@asynccontextmanager
async def connect(server, timeout=30):
    timeout = min(timeout, server["timeout_seconds"])
    cfg = settings()
    failure = None
    async with AsyncExitStack() as stack:
        async with asyncio.timeout(timeout):
            if server["transport"] == "stdio":
                cwd = server.get("cwd")
                if cwd:
                    cwd = str((cfg.config_root.parent / cfg.resolve(cwd)).resolve())
                params = StdioServerParameters(
                    command=cfg.resolve(server["command"]),
                    args=[cfg.resolve(arg) for arg in server["args"]],
                    env={key: cfg.resolve(value) for key, value in server["env"].items()},
                    cwd=cwd,
                )
                streams = await stack.enter_async_context(stdio_client(params))
            else:
                url = cfg.resolve(server["url"])
                parsed = urlsplit(url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                    raise Problem("MCP_UNAVAILABLE", "MCP 连接配置不可用", 503)
                headers = {key: cfg.resolve(value) for key, value in server["headers"].items()}
                headers = {key: value for key, value in headers.items() if value.strip()}
                client = await stack.enter_async_context(
                    httpx2.AsyncClient(headers=headers, timeout=timeout, trust_env=False)
                )
                streams = await stack.enter_async_context(streamable_http_client(url, http_client=client))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
        try:
            yield session
        except BaseException as exc:
            # Do not pass application errors through SDK TaskGroups: they would
            # wrap validation errors/cancellation in nested exception groups.
            failure = exc
    if failure is not None:
        raise failure


async def discover(session, ident, server, timeout=30):
    tools = []
    cursor = None
    seen = set()
    async with asyncio.timeout(min(timeout, server["timeout_seconds"])):
        for _ in range(20):
            page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            raw = page.model_dump(by_alias=True, mode="json")
            for tool in raw["tools"]:
                if tool["name"] not in server["allowed_tools"]:
                    continue
                schema = tool.get("inputSchema", {"type": "object"})
                Draft202012Validator.check_schema(schema)
                tools.append(
                    {
                        "server_id": ident,
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "input_schema": schema,
                    }
                )
            cursor = raw.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen:
                break
            seen.add(cursor)
    raise Problem("MCP_UNAVAILABLE", "MCP 工具目录不可用", 503)


async def invoke(session, server, tool, arguments, timeout=30):
    if tool["name"] not in server["allowed_tools"]:
        raise Problem("MCP_TOOL_FORBIDDEN", "此工具未由后台开放", 403)
    try:
        Draft202012Validator(tool["input_schema"]).validate(arguments)
    except ValidationError:
        raise Problem("MCP_ARGUMENTS_INVALID", "工具参数不符合接口要求", 422) from None
    async with asyncio.timeout(min(timeout, server["timeout_seconds"])):
        result = await session.call_tool(tool["name"], arguments)
    raw = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    if raw.get("isError"):
        raise Problem("MCP_TOOL_FAILED", "工具执行未完成，请稍后重试", 503)
    if len(json.dumps(raw, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        raise Problem("MCP_RESULT_LIMIT", "工具结果超过容量，请缩小查询", 409)
    return raw


def text_result(result):
    return "\n".join(block.get("text", "") for block in result.get("content", []) if block["type"] == "text")


def function_name(tool):
    # Stable, collision-resistant names fit provider function name limits.
    digest = hashlib.sha256((tool["server_id"] + ":" + tool["name"]).encode()).hexdigest()[:12]
    return "mcp_" + digest


def function_schema(tool):
    return {
        "type": "function",
        "function": {
            "name": function_name(tool),
            "description": f"{tool['server_id']}/{tool['name']}: {tool['description']}",
            "parameters": tool["input_schema"],
        },
    }


async def check(ident, server):
    result = descriptor(ident, server)
    if not server["enabled"]:
        return result
    try:
        async with connect(server) as session:
            tools = await discover(session, ident, server)
        return {**result, "status": "connected", "tool_count": len(tools)}
    except asyncio.CancelledError:
        raise
    except Exception:
        return {**result, "status": "unavailable", "tool_count": 0}
