"""Opt-in real provider probes; writes only non-secret capability evidence."""

import asyncio
import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import httpx
from PIL import Image

from study_trail.config import settings


async def main():
    cfg = settings()
    report = {}
    capabilities = {}
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(image, "PNG")
    image_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        for ident in ["gemini-3.7-flash-high", "gemini-3.8-flash-high"]:
            checks = {}
            headers = {"Authorization": f"Bearer {cfg.generation_api_key}"}

            async def complete(messages, **options):
                response = await client.post(
                    cfg.generation_base_url + "/chat/completions",
                    headers=headers,
                    json={"model": ident, "messages": messages, "max_tokens": 256, **options},
                )
                response.raise_for_status()
                return response.json()

            try:
                out = await complete([{"role": "user", "content": "仅回复：学迹联调成功"}])
                checks["text"] = bool(out["choices"][0]["message"].get("content"))
                checks["text_usage"] = out.get("usage", {})
                print(ident, "text", checks["text"], flush=True)
                buf = ""
                async with client.stream(
                    "POST",
                    cfg.generation_base_url + "/chat/completions",
                    headers=headers,
                    json={
                        "model": ident,
                        "messages": [{"role": "user", "content": "请用一句话解释余弦相似度。"}],
                        "stream": True,
                        "max_tokens": 256,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data:") and line[5:].strip() != "[DONE]":
                            data = json.loads(line[5:])
                            choices = data.get("choices", [])
                            if choices:
                                buf += choices[0].get("delta", {}).get("content") or ""
                checks["stream"] = bool(buf)
                print(ident, "stream", checks["stream"], flush=True)
                content = [{"type": "text", "text": "这些图片主要是什么颜色？仅回答颜色。"}] + [
                    {"type": "image_url", "image_url": {"url": image_url}} for _ in range(6)
                ]
                out = await complete([{"role": "user", "content": content}])
                text = out["choices"][0]["message"].get("content", "")
                checks["images"] = "红" in text or "red" in text.lower()
                checks["image_count_tested"] = 6
                checks["image_usage"] = out.get("usage", {})
                print(ident, "images", checks["images"], flush=True)
                try:
                    out = await complete(
                        [
                            {
                                "role": "user",
                                "content": '返回 JSON 对象 {"ok":true,"message":"学迹"}，不要额外文字。',
                            }
                        ],
                        response_format={"type": "json_object"},
                    )
                    parsed = json.loads(out["choices"][0]["message"]["content"])
                    checks["json_object"] = parsed.get("ok") is True
                except (httpx.HTTPError, ValueError, KeyError):
                    checks["json_object"] = False
                # Establish a tested conservative operating budget, not the model's advertised maximum.
                out = await complete(
                    [
                        {
                            "role": "user",
                            "content": "阅读以下测试文本，然后仅回答收到。\n" + (" learning" * 40000),
                        }
                    ]
                )
                usage = out.get("usage", {})
                checks["budget_probe_prompt_tokens"] = usage.get("prompt_tokens")
                checks["budget"] = bool(out["choices"][0]["message"].get("content"))
                tested = usage.get("prompt_tokens", 0)
                input_budget = min(32768, tested) if tested >= 4096 else 4096
                capabilities[ident] = {
                    "enabled": checks["text"] and checks["stream"] and checks["budget"],
                    "supports_images": checks["images"],
                    "input_token_budget": input_budget,
                    "max_output_tokens": 4096,
                    "max_images": 6 if checks["images"] else 0,
                    "max_image_bytes": 62914560 if checks["images"] else 0,
                    "json_object": checks["json_object"],
                    "verified_at": "2026-10-05",
                    "budget_basis": "conservative tested operating limit",
                }
                print(ident, "budget", input_budget, "json", checks["json_object"], flush=True)
            except Exception as exc:
                checks["failure_type"] = type(exc).__name__
                if isinstance(exc, httpx.HTTPStatusError):
                    checks["http_status"] = exc.response.status_code
                print(ident, "failed", checks["failure_type"], flush=True)
            report[ident] = checks
        headers = {"Authorization": f"Bearer {cfg.siliconflow_api_key}"}
        for name, path, payload in [
            (
                "embedding",
                "embeddings",
                {
                    "model": "BAAI/bge-m3",
                    "input": ["学迹测试资料：余弦相似度衡量向量方向的接近程度。"],
                    "encoding_format": "float",
                },
            ),
            (
                "rerank",
                "rerank",
                {
                    "model": "BAAI/bge-reranker-v2-m3",
                    "query": "余弦相似度是什么",
                    "documents": ["余弦相似度衡量向量方向的接近程度。", "矩阵乘法需要维度匹配。"],
                    "top_n": 1,
                },
            ),
        ]:
            try:
                response = await client.post(
                    "https://api.siliconflow.cn/v1/" + path, headers=headers, json=payload
                )
                response.raise_for_status()
                out = response.json()
                report[name] = (
                    {
                        "ok": len(out["data"][0]["embedding"]) == 1024,
                        "dimensions": len(out["data"][0]["embedding"]),
                    }
                    if name == "embedding"
                    else {"ok": out["results"][0]["index"] == 0}
                )
            except Exception as exc:
                report[name] = {"ok": False, "failure_type": type(exc).__name__}
                if isinstance(exc, httpx.HTTPStatusError):
                    report[name]["http_status"] = exc.response.status_code
            print(name, report[name], flush=True)
    cfg.model_capabilities_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.model_capabilities_file.write_text(
        json.dumps(capabilities, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    target = Path(__file__).parents[2] / "docs/provider-probes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
