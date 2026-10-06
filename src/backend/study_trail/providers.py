import asyncio
import base64
import io
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image

from .config import settings
from .core import Problem, storage_path
from .db import now


def json_result(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def input_estimate(text):
    # Conservative budget estimate: each CJK character and punctuation may cost a token.
    ascii_letters = sum(c.isascii() and (c.isalnum() or c.isspace()) for c in text)
    return len(text) - ascii_letters + (ascii_letters + 2) // 3


def image_content(row):
    with Image.open(storage_path(row["storage_key"])) as image:
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=85)
    return {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()},
    }


class Gateway:
    def __init__(self, task):
        self.task = task

    def timeout(self, maximum):
        remaining = (self.task["deadline_at"] - now()).total_seconds()
        if remaining <= 0:
            raise Problem("TASK_TIMEOUT", "任务处理超时，请重试", 409)
        return min(maximum, remaining)

    def connection(self):
        cfg = settings()
        provider = self.task["config_snapshot"].get(
            "provider", {"base_url": "{env:GENERATION_BASE_URL}", "api_key": "{env:GENERATION_API_KEY}"}
        )
        url = cfg.resolve(provider["base_url"]).rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise Problem("MODEL_UNAVAILABLE", "模型连接配置不可用", 503)
        return url, {"Authorization": "Bearer " + cfg.resolve(provider["api_key"])}

    async def tool_turn(self, messages, tools):
        model = self.task["config_snapshot"]
        url, headers = self.connection()
        payload = {
            "model": model.get("api_model_id", model["model_id"]),
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": model.get("max_output_tokens", 4096),
            "stream": False,
        }
        try:
            async with asyncio.timeout(self.timeout(120)):
                async with httpx.AsyncClient(timeout=self.timeout(120), trust_env=False) as client:
                    response = await client.post(url + "/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    choice = response.json()["choices"][0]
            if choice.get("finish_reason") == "length":
                raise Problem("MODEL_OUTPUT_LIMIT", "工具选择超过输出容量", 409)
            message = choice["message"]
            calls = message.get("tool_calls", [])
            seen = set()
            for call in calls:
                if not isinstance(call.get("id"), str) or not call["id"] or call["id"] in seen:
                    raise ValueError()
                seen.add(call["id"])
                if call.get("type") != "function" or not isinstance(call["function"]["arguments"], str):
                    raise ValueError()
            return {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
        except (httpx.TimeoutException, TimeoutError):
            raise Problem("MODEL_TIMEOUT", "模型响应超时，请稍后重试", 409) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise Problem("MODEL_UNAVAILABLE", "模型工具选择暂时不可用", 503) from None

    async def complete(self, messages, json_mode=False, on_chunk=None):
        model = self.task["config_snapshot"]
        url, headers = self.connection()
        payload = {
            "model": model.get("api_model_id", model["model_id"]),
            "messages": messages,
            "max_tokens": model.get("max_output_tokens", 4096),
            "stream": on_chunk is not None,
        }
        if json_mode and model.get("json_object"):
            payload["response_format"] = {"type": "json_object"}
        try:
            async with asyncio.timeout(self.timeout(120)):
                async with httpx.AsyncClient(timeout=self.timeout(120), trust_env=False) as client:
                    if not on_chunk:
                        response = await client.post(url + "/chat/completions", headers=headers, json=payload)
                        response.raise_for_status()
                        choice = response.json()["choices"][0]
                        if choice.get("finish_reason") == "length":
                            raise Problem("MODEL_OUTPUT_LIMIT", "模型输出超过容量，请缩小请求", 409)
                        return choice["message"].get("content") or ""
                    text = ""
                    async with client.stream(
                        "POST", url + "/chat/completions", headers=headers, json=payload
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            event = json.loads(raw)
                            choices = event.get("choices", [])
                            if not choices:
                                continue
                            if choices[0].get("finish_reason") == "length":
                                raise Problem("MODEL_OUTPUT_LIMIT", "模型输出超过容量，请缩小请求", 409)
                            chunk = choices[0].get("delta", {}).get("content") or ""
                            if chunk:
                                text += chunk
                                await on_chunk(text)
                    if not text.strip():
                        raise Problem("MODEL_EMPTY_OUTPUT", "模型没有返回可用内容", 409)
                    return text
        except (httpx.TimeoutException, TimeoutError):
            raise Problem("MODEL_TIMEOUT", "模型响应超时，请稍后重试", 409)
        except (httpx.HTTPError, KeyError, ValueError):
            raise Problem("MODEL_UNAVAILABLE", "模型服务暂时不可用，请稍后重试", 503)

    async def structured(self, prompt, messages=None):
        base = messages or []
        text = await self.complete(
            [*base, {"role": "user", "content": prompt + "\n只返回 JSON 对象，不使用 Markdown 围栏。"}],
            json_mode=True,
        )
        return json_result(text)

    async def silicon(self, path, payload):
        try:
            async with asyncio.timeout(self.timeout(30)):
                async with httpx.AsyncClient(timeout=self.timeout(30), trust_env=False) as client:
                    response = await client.post(
                        "https://api.siliconflow.cn/v1/" + path,
                        headers={"Authorization": f"Bearer {settings().siliconflow_api_key}"},
                        json=payload,
                    )
                    response.raise_for_status()
                    return response.json()
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise Problem(
                "EMBEDDING_FAILED" if path == "embeddings" else "RERANK_FAILED", "资料模型服务暂时不可用", 503
            )

    async def embed(self, texts):
        data = await self.silicon(
            "embeddings", {"model": "BAAI/bge-m3", "input": texts, "encoding_format": "float"}
        )
        rows = sorted(data["data"], key=lambda r: r["index"])
        if len(rows) != len(texts) or any(len(r["embedding"]) != 1024 for r in rows):
            raise Problem("EMBEDDING_FAILED", "嵌入结果维度不符合索引要求", 503)
        return [r["embedding"] for r in rows]

    async def rerank(self, query, docs):
        data = await self.silicon(
            "rerank", {"model": "BAAI/bge-reranker-v2-m3", "query": query, "documents": docs, "top_n": 6}
        )
        indices = [r["index"] for r in data["results"]]
        if len(indices) != len(set(indices)) or any(
            not isinstance(i, int) or not 0 <= i < len(docs) for i in indices
        ):
            raise Problem("RERANK_FAILED", "重排返回了无效结果", 503)
        return indices

    async def search(self, query):
        from . import mcp_tools

        server = self.task["config_snapshot"].get("mcp_servers", {}).get("exa")
        if not server:
            return []
        async with mcp_tools.connect(server, self.timeout(30)) as session:
            tools = await mcp_tools.discover(session, "exa", server, self.timeout(30))
            tool = next((tool for tool in tools if tool["name"] == "web_search_exa"), None)
            if tool is None:
                raise Problem("MCP_UNAVAILABLE", "搜索工具暂时不可用", 503)
            arguments = {"query": query, "numResults": 5}
            if "objective" in tool["input_schema"].get("required", []):
                arguments["objective"] = "检索可核对的教育资料，优先可靠来源，回答：" + query
            result = await mcp_tools.invoke(session, server, tool, arguments, self.timeout(30))
        return parse_search(mcp_tools.text_result(result))


def parse_search(text):
    entries = []
    # Official MCP currently provides text. Accept URL/Title labels, not invented JSON results.
    for block in re.split(r"(?=^Title:)", text, flags=re.MULTILINE):
        title = re.search(r"^Title:\s*(.+)$", block, re.MULTILINE)
        url = re.search(r"^URL:\s*(https?://\S+)", block, re.MULTILINE)
        excerpt = re.search(r"^(?:Highlights|Text|Content|Summary):\s*([\s\S]+)", block, re.MULTILINE)
        if not title or not url or not excerpt:
            continue
        parsed = urlsplit(url.group(1))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            continue
        snippet = excerpt.group(1).split("\n---")[0].strip()[:4000]
        if not snippet:
            continue
        entries.append(
            {
                "id": str(uuid4()),
                "kind": "web",
                "title": title.group(1).strip(),
                "url": url.group(1),
                "excerpt": snippet,
                "file_id": None,
                "chunk_id": None,
                "locator": None,
                "status": "external",
                "captured_at": now().isoformat(),
            }
        )
        if len(entries) == 5:
            break
    return entries
