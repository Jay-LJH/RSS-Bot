import json
from typing import Any

from llm_client import summarize_daily_report
from sections.new import get_new_section
from sections.tech import get_tech_section
from sections.trend import build_trend_payload, get_trending


class ReportGenerationError(RuntimeError):
    pass


VALID_MODULES = ("new", "tech", "trend")


def _summarize_with_ai(
    repo_payload: list[dict[str, str]],
    tech_payload: list[dict[str, str]],
    new_payload: list[dict[str, str]],
) -> dict[str, Any]:
    try:
        return summarize_daily_report(repo_payload, tech_payload, new_payload)
    except Exception as exc:
        raise ReportGenerationError(
            "AI 总结失败（已按 Gemini -> DeepSeek 顺序尝试）："
            f"{exc}"
        ) from exc


def _fill_summaries(
    data: dict[str, Any],
    repos: list[dict[str, str]],
    tech_items: list[dict[str, str]],
    new_items: list[dict[str, str]],
) -> dict[str, Any]:
    github_summaries = data.get("github_summaries")
    tech_summaries = data.get("tech_summaries")
    new_summaries = data.get("news_summaries")

    if not isinstance(github_summaries, list):
        github_summaries = []
    if not isinstance(tech_summaries, list):
        tech_summaries = []
    if not isinstance(new_summaries, list):
        new_summaries = []

    github_summaries = [str(x) for x in github_summaries][: len(repos)]
    tech_summaries = [str(x) for x in tech_summaries][: len(tech_items)]
    new_summaries = [str(x) for x in new_summaries][: len(new_items)]

    while len(github_summaries) < len(repos):
        repo = repos[len(github_summaries)]
        github_summaries.append(
            f"{repo['full_name']}：{repo.get('desc') or '该仓库暂未提供简介。'}"
        )

    while len(tech_summaries) < len(tech_items):
        item = tech_items[len(tech_summaries)]
        tech_summaries.append(f"{item['title']}：{item.get('snippet') or '暂无摘要。'}")

    while len(new_summaries) < len(new_items):
        item = new_items[len(new_summaries)]
        new_summaries.append(f"{item['title']}：{item.get('snippet') or '暂无摘要。'}")

    return {
        "github_summaries": github_summaries,
        "github_trend": str(data.get("github_trend") or "开源项目持续强调工程化落地与效率提升。"),
        "tech_summaries": tech_summaries,
        "tech_trend": str(data.get("tech_trend") or "科技动态聚焦 AI 能力向真实业务场景的迁移。"),
        "new_summaries": new_summaries,
        "new_focus": str(data.get("news_focus") or "国际新闻仍集中在地缘政治、经济与科技监管。"),
    }


def _normalize_modules(modules: list[str] | None) -> list[str]:
    if not modules:
        return list(VALID_MODULES)

    normalized: list[str] = []
    for item in modules:
        key = (item or "").strip().lower()
        if key == "news":
            key = "new"
        if key in VALID_MODULES and key not in normalized:
            normalized.append(key)

    return normalized


def get_report(modules: list[str] | None = None, limit: int = 3) -> str:
    enabled_modules = _normalize_modules(modules)
    if not enabled_modules:
        raise ReportGenerationError("未指定有效板块，可选：new、tech、trend")

    repos: list[dict[str, str]] = []
    repo_payload: list[dict[str, str]] = []
    tech_items: list[dict[str, str]] = []
    new_items: list[dict[str, str]] = []

    if "trend" in enabled_modules:
        try:
            repos = get_trending(limit=limit)
        except Exception as exc:
            raise ReportGenerationError(f"抓取 Trend 板块失败：{exc}") from exc

        if not repos:
            raise ReportGenerationError("Trend 板块未返回可解析的仓库数据。")

        try:
            repo_payload = build_trend_payload(repos)
        except Exception as exc:
            raise ReportGenerationError(f"构建 Trend 板块摘要输入失败：{exc}") from exc

    if "tech" in enabled_modules:
        try:
            tech_items = get_tech_section(limit=5)
        except Exception as exc:
            raise ReportGenerationError(f"抓取 Tech 板块失败：{exc}") from exc

        if len(tech_items) < 1:
            raise ReportGenerationError("Tech 板块可用信息不足。")

    if "new" in enabled_modules:
        try:
            new_items = get_new_section(limit=5)
        except Exception as exc:
            raise ReportGenerationError(f"抓取 New 板块失败：{exc}") from exc

        if len(new_items) < 1:
            raise ReportGenerationError("New 板块可用信息不足。")

    raw_summary = _summarize_with_ai(repo_payload, tech_items, new_items)
    summary = _fill_summaries(raw_summary, repos, tech_items, new_items)

    lines = ["🧾 今日推送", f"板块：{', '.join(enabled_modules)}", ""]

    if "trend" in enabled_modules:
        lines.append("📈 Trend 板块（GitHub Trending）")
        lines.append("")
        for idx, repo in enumerate(repos, 1):
            lines.append(f"{idx}. {repo['full_name']}")
            lines.append(f"链接：{repo['url']}")
            lines.append(f"用途总结：{summary['github_summaries'][idx - 1]}")
            lines.append("")

        lines.append(f"🧭 Trend 焦点：{summary['github_trend']}")
        lines.append("")

    if "tech" in enabled_modules:
        lines.append("🧪 Tech 板块")
        lines.append("")
        for idx, item in enumerate(tech_items, 1):
            lines.append(f"{idx}. [{item['source']}] {item['title']}")
            lines.append(f"链接：{item['url']}")
            lines.append(f"AI 总结：{summary['tech_summaries'][idx - 1]}")
            lines.append("")

        lines.append(f"🔬 Tech 焦点：{summary['tech_trend']}")
        lines.append("")

    if "new" in enabled_modules:
        lines.append("📰 New 板块")
        lines.append("")
        for idx, item in enumerate(new_items, 1):
            lines.append(f"{idx}. [{item['source']}] {item['title']}")
            lines.append(f"链接：{item['url']}")
            lines.append(f"AI 总结：{summary['new_summaries'][idx - 1]}")
            lines.append("")

        lines.append(f"🌍 New 焦点：{summary['new_focus']}")

    return "\n".join(lines)


def get_today_report(limit: int = 3) -> str:
    return get_report(modules=["new", "tech", "trend"], limit=limit)