from __future__ import annotations

import json
import math
import re
from hashlib import blake2b
from typing import Any

import requests
from google import genai

from config import get_env
from core.article import Article


def _extract_json_block(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _summarize_with_gemini(prompt_text: str) -> str:
    gemini_key = (get_env("GEMINI_API_KEY") or get_env("GOOGLE_API_KEY")).strip()
    if not gemini_key:
        raise RuntimeError(".env 中未配置 GEMINI_API_KEY 或 GOOGLE_API_KEY")

    model_candidates = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview"]
    client = genai.Client(api_key=gemini_key)
    last_error: Exception | None = None

    for model in model_candidates:
        try:
            response = client.models.generate_content(model=model, contents=prompt_text)
            content = response.text or ""
            if content.strip():
                return content
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Gemini 调用失败，最后错误：{last_error}") from last_error


def _summarize_with_deepseek(prompt_text: str) -> str:
    deepseek_key = get_env("DEEPSEEK_API_KEY").strip()
    if not deepseek_key:
        raise RuntimeError(".env 中未配置 DEEPSEEK_API_KEY")

    url = (get_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    response = requests.post(
        f"{url}/chat/completions",
        headers={"Authorization": f"Bearer {deepseek_key}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是一个擅长技术信息提炼的助手，只输出 JSON。"},
                {"role": "user", "content": prompt_text},
            ],
            "temperature": 0.2,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"DeepSeek 返回异常：{payload}")

    content = ((choices[0] or {}).get("message") or {}).get("content") or ""
    if not content.strip():
        raise RuntimeError("DeepSeek 返回内容为空")
    return content


def _invoke_json_prompt(prompt: dict[str, Any], fast: bool = False) -> dict[str, Any]:
    role = "工具路由助手" if fast else "擅长技术信息提炼的助手"
    prompt_text = f"你是一个{role}，只输出用户要求的 JSON。\n" + json.dumps(prompt, ensure_ascii=False)

    errors: list[str] = []
    for fn_name, fn in (("Gemini", _summarize_with_gemini), ("DeepSeek", _summarize_with_deepseek)):
        try:
            data = _extract_json_block(fn(prompt_text))
            if data:
                return data
            errors.append(f"{fn_name} 返回内容不是有效 JSON")
        except Exception as exc:
            errors.append(f"{fn_name} 失败：{exc}")

    raise RuntimeError("；".join(errors))


def _invoke_text_prompt(prompt_text: str) -> str:
    errors: list[str] = []
    for fn_name, fn in (("Gemini", _summarize_with_gemini), ("DeepSeek", _summarize_with_deepseek)):
        try:
            content = fn(prompt_text).strip()
            if content:
                return content
            errors.append(f"{fn_name} 返回空内容")
        except Exception as exc:
            errors.append(f"{fn_name} 失败：{exc}")
    raise RuntimeError("；".join(errors))


def _tokenize_for_embedding(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", (text or "").lower()) if t]


def _local_hash_embedding(text: str, dims: int = 256) -> list[float]:
    vec = [0.0] * max(32, int(dims))
    for token in _tokenize_for_embedding(text):
        digest = blake2b(token.encode("utf-8"), digest_size=16).digest()
        idx = int.from_bytes(digest[:4], "big") % len(vec)
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign * (1.0 + ((digest[5] % 5) / 10.0))

    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 1e-12:
        return vec
    return [x / norm for x in vec]


def _embed_with_gemini(text: str) -> tuple[list[float], str]:
    gemini_key = (get_env("GEMINI_API_KEY") or get_env("GOOGLE_API_KEY")).strip()
    if not gemini_key:
        raise RuntimeError(".env 中未配置 GEMINI_API_KEY 或 GOOGLE_API_KEY")

    client = genai.Client(api_key=gemini_key)
    model_candidates = ["text-embedding-004", "gemini-embedding-001"]
    last_error: Exception | None = None

    for model in model_candidates:
        try:
            resp = client.models.embed_content(model=model, contents=text)
            values = None
            if hasattr(resp, "embeddings") and getattr(resp, "embeddings"):
                values = getattr(resp.embeddings[0], "values", None)
            if values is None and hasattr(resp, "embedding"):
                values = getattr(getattr(resp, "embedding"), "values", None)
            if values:
                return [float(x) for x in values], model
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Gemini Embedding 调用失败，最后错误：{last_error}") from last_error


def embed_text(text: str) -> dict[str, Any]:
    clean = (text or "").strip()
    if not clean:
        return {"vector": [], "model": "empty"}
    try:
        vec, model = _embed_with_gemini(clean)
        return {"vector": vec, "model": model}
    except Exception:
        return {"vector": _local_hash_embedding(clean, dims=256), "model": "local-hash-256"}


def embed_article(article: Article) -> Article:
    text = "\n".join(
        [
            f"module: {article.module}",
            f"source: {article.source_name}",
            f"title: {article.title}",
            f"snippet: {article.snippet}",
            f"url: {article.url}",
        ]
    ).strip()
    payload = embed_text(text)
    article.embedding = [float(x) for x in payload.get("vector", []) if isinstance(x, (int, float))]
    article.embedding_model = str(payload.get("model") or "")
    return article


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    size = min(len(vec_a), len(vec_b))
    if size <= 0:
        return 0.0
    a = vec_a[:size]
    b = vec_b[:size]
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


def summarize_generic_section(module: str, module_title: str, items: list[Article]) -> dict[str, Any]:
    prompt = {
        "task": "为通用资讯板块生成摘要",
        "requirements": [
            "对每条内容输出 3 句核心信息总结",
            "输出一句板块焦点总结",
            "严格输出 JSON，格式为 {\"summaries\":[],\"focus\":\"\"}",
        ],
        "module": module,
        "module_title": module_title,
        "items": [
            {"title": a.title, "snippet": a.snippet, "url": a.url, "source": a.source_name}
            for a in items
        ],
    }
    return _invoke_json_prompt(prompt)


def classify_rss_feed(source_name: str, feed_title: str, samples: list[dict[str, str]]) -> dict[str, str]:
    prompt = {
        "task": "根据 RSS 源内容为其归类模块",
        "requirements": [
            "module_key 使用英文小写与下划线",
            "module_title 使用中文名称",
            "reason 用一句中文简述分类依据",
            "严格输出 JSON：{\"module_key\":\"\",\"module_title\":\"\",\"reason\":\"\"}",
        ],
        "source_name": source_name,
        "feed_title": feed_title,
        "samples": samples,
    }
    data = _invoke_json_prompt(prompt)
    return {
        "module_key": str(data.get("module_key") or "").strip().lower().replace("-", "_"),
        "module_title": str(data.get("module_title") or "").strip(),
        "reason": str(data.get("reason") or "").strip(),
    }


def plan_tool_call(user_message: str, tool_schemas: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = {
        "task": "根据用户输入选择是否调用工具。",
        "rules": [
            "如果用户在询问资讯/日报/新闻/趋势，优先返回 tool_call",
            "优先选择 get_semantic_articles",
            "必须从提供 tools 中选择 tool_name",
            "若无需工具则返回 chat 并给出简短 reply",
            "严格输出 JSON：{\"mode\":\"tool_call|chat\",\"tool_name\":\"\",\"arguments\":{},\"reply\":\"\"}",
        ],
        "tools": tool_schemas,
        "user_message": user_message,
    }
    data = _invoke_json_prompt(prompt)
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in {"tool_call", "chat"}:
        mode = "chat"
    return {
        "mode": mode,
        "tool_name": str(data.get("tool_name") or "").strip(),
        "arguments": data.get("arguments") if isinstance(data.get("arguments"), dict) else {},
        "reply": str(data.get("reply") or "").strip(),
    }


def plan_tool_call_small_model(user_message: str, tool_schemas: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = {
        "task": "快速判断是否调用工具",
        "tools": tool_schemas,
        "user_message": user_message,
        "rules": ["优先资讯场景返回 tool_call", "优先 get_semantic_articles", "严格输出 JSON"],
    }
    data = _invoke_json_prompt(prompt, fast=True)
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in {"tool_call", "chat"}:
        mode = "chat"
    return {
        "mode": mode,
        "tool_name": str(data.get("tool_name") or "").strip(),
        "arguments": data.get("arguments") if isinstance(data.get("arguments"), dict) else {},
        "reply": str(data.get("reply") or "").strip(),
    }


def generate_user_reply(user_message: str, tool_result: str) -> str:
    prompt_text = (
        "你是一个 Telegram 助手。请基于工具返回结果回复用户。\n"
        "要求：使用中文，保留关键信息与链接，简洁。\n"
        f"用户消息：{user_message}\n"
        f"工具结果：\n{tool_result}\n"
    )
    return _invoke_text_prompt(prompt_text)
