import json
from typing import Any

import requests
from google import genai

from config import get_env


def _extract_json_block(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _build_prompt(
    repo_payload: list[dict[str, str]],
    tech_payload: list[dict[str, str]],
    news_payload: list[dict[str, str]],
) -> str:
    prompt = {
        "task": "请基于输入内容生成日报摘要。",
        "requirements": [
            "GitHub 仓库：每条 1-2 句，强调用途与适用场景",
            "科技板块：每条 1 句总结其核心信息",
            "新闻板块：每条 1 句总结其核心信息",
            "输出一句当天技术趋势总结，和一句当天国际新闻焦点总结",
            "严格输出 JSON，格式为 {\"github_summaries\":[],\"github_trend\":\"\",\"tech_summaries\":[],\"tech_trend\":\"\",\"news_summaries\":[],\"news_focus\":\"\"}",
        ],
        "repos": repo_payload,
        "tech_items": tech_payload,
        "news_items": news_payload,
    }

    return (
        "你是一个擅长技术信息提炼的助手，只输出用户要求的 JSON。\n"
        + json.dumps(prompt, ensure_ascii=False)
    )


def _summarize_with_gemini(prompt_text: str) -> str:
    gemini_key = (get_env("GEMINI_API_KEY") or get_env("GOOGLE_API_KEY")).strip()
    if not gemini_key:
        raise RuntimeError(".env 中未配置 GEMINI_API_KEY 或 GOOGLE_API_KEY")

    model_candidates = [
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
    ]

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

    raise RuntimeError(
        "Gemini 调用失败："
        f"已尝试模型 {model_candidates}，"
        f"最后错误：{last_error}"
    ) from last_error


def _summarize_with_deepseek(prompt_text: str) -> str:
    deepseek_key = get_env("DEEPSEEK_API_KEY").strip()
    if not deepseek_key:
        raise RuntimeError(".env 中未配置 DEEPSEEK_API_KEY")

    url = (get_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    response = requests.post(
        f"{url}/chat/completions",
        headers={
            "Authorization": f"Bearer {deepseek_key}",
            "Content-Type": "application/json",
        },
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


def summarize_daily_report(
    repo_payload: list[dict[str, str]],
    tech_payload: list[dict[str, str]],
    news_payload: list[dict[str, str]],
) -> dict[str, Any]:
    prompt_text = _build_prompt(repo_payload, tech_payload, news_payload)

    errors: list[str] = []

    try:
        content = _summarize_with_gemini(prompt_text)
        data = _extract_json_block(content)
        if data:
            return data
        errors.append(f"Gemini 返回内容不是有效 JSON：{content[:200]}")
    except Exception as exc:
        errors.append(f"Gemini 失败：{exc}")

    try:
        content = _summarize_with_deepseek(prompt_text)
        data = _extract_json_block(content)
        if data:
            return data
        errors.append(f"DeepSeek 返回内容不是有效 JSON：{content[:200]}")
    except Exception as exc:
        errors.append(f"DeepSeek 失败：{exc}")

    raise RuntimeError("；".join(errors))
