import logging
import json
import random
from pathlib import Path
from datetime import time, timezone, timedelta
from typing import Any
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import get_required_env
from crawler import get_report, refresh_content_cache
from llm_client import generate_user_reply, plan_tool_call_small_model
from mcp_tools import MCPToolRegistry, create_default_registry
from source_registry import (
    add_rss_source,
    get_default_modules,
    list_modules,
    match_modules_by_rules,
    normalize_module_key,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

SUBSCRIPTIONS_FILE = Path(__file__).resolve().parent / "subscriptions.json"
CN_TZ = timezone(timedelta(hours=8))


def _default_modules() -> list[str]:
    modules = get_default_modules()
    if modules:
        return modules
    existing = list_modules()
    return existing[:3] if existing else []


def _split_message(text: str, max_len: int = 3800) -> list[str]:
    if len(text) <= max_len:
        return [text]

    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= max_len:
            current = candidate
            continue

        if current:
            parts.append(current)
            current = block
        else:
            parts.append(block[:max_len])
            current = block[max_len:]

    if current:
        parts.append(current)

    return parts


def _get_registry(app: Application) -> MCPToolRegistry:
    registry = app.bot_data.get("mcp_registry")
    if isinstance(registry, MCPToolRegistry):
        return registry

    registry = create_default_registry()
    app.bot_data["mcp_registry"] = registry
    return registry


def _fallback_tool_decision(text: str) -> dict[str, Any]:
    query = text.strip()
    low = query.lower()
    if query and any(k in low for k in ["今天", "日报", "汇总", "总结", "推送", "rss", "资讯", "新闻", "市场", "体育", "财经"]):
        return {
            "mode": "tool_call",
            "tool_name": "get_semantic_articles",
            "arguments": {"query": query, "top_k": 5, "min_similarity": 0.25},
            "reply": "",
        }
    if query:
        return {
            "mode": "tool_call",
            "tool_name": "get_semantic_articles",
            "arguments": {"query": query, "top_k": 5, "min_similarity": 0.2},
            "reply": "",
        }
    return {
        "mode": "chat",
        "tool_name": "",
        "arguments": {},
        "reply": "我可以帮你根据问题自动匹配信息源并推送内容。输入 /help 查看命令。",
    }


def _help_text() -> str:
    available = list_modules()
    modules = ", ".join(available) if available else "暂无（先用 /rss 添加）"
    return (
        "📘 使用帮助\n"
        "1) 添加 RSS 源：/rss <rss_url> [名称]\n"
        "2) 手动推送：/send [模块1,模块2]\n"
        "3) 自动推送：/autopush on|off|modules\n"
        "4) 自然语言提问：直接输入想看的内容（会自动匹配源）\n"
        f"当前可用模块：{modules}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(_help_text())

def _normalize_modules(raw: str | list[str] | None) -> list[str]:
    available = set(list_modules())
    if raw is None:
        return _default_modules()

    if isinstance(raw, list):
        parts = [str(x).strip().lower() for x in raw]
    else:
        text = raw.replace("，", ",").replace("|", ",")
        parts = [x.strip().lower() for x in text.split(",") if x.strip()]

    normalized: list[str] = []
    for item in parts:
        key = normalize_module_key(item)
        if key in available and key not in normalized:
            normalized.append(key)

    return normalized


def _load_subscriptions() -> dict[int, dict[str, Any]]:
    if not SUBSCRIPTIONS_FILE.exists():
        return {}

    try:
        data = json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))

        # 兼容旧格式: {"chat_ids": [123, 456]}
        if isinstance(data, dict) and isinstance(data.get("chat_ids"), list):
            result: dict[int, dict[str, Any]] = {}
            for chat_id in data["chat_ids"]:
                result[int(chat_id)] = {
                    "enabled": True,
                    "modules": _default_modules(),
                }
            return result

        subscriptions = data.get("subscriptions", {}) if isinstance(data, dict) else {}
        result: dict[int, dict[str, Any]] = {}
        if isinstance(subscriptions, dict):
            for chat_id, cfg in subscriptions.items():
                if not isinstance(cfg, dict):
                    continue
                modules = _normalize_modules(cfg.get("modules"))
                if not modules:
                    modules = _default_modules()
                result[int(chat_id)] = {
                    "enabled": bool(cfg.get("enabled", True)),
                    "modules": modules,
                }
        return result
    except Exception:
        logger.exception("读取订阅文件失败")
        return {}


def _save_subscriptions(subscriptions: dict[int, dict[str, Any]]) -> None:
    payload = {
        "subscriptions": {
            str(chat_id): {
                "enabled": bool(cfg.get("enabled", True)),
                "modules": _normalize_modules(cfg.get("modules")) or _default_modules(),
            }
            for chat_id, cfg in sorted(subscriptions.items())
        }
    }
    SUBSCRIPTIONS_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _job_name(chat_id: int) -> str:
    return f"daily_push_{chat_id}"


def _schedule_daily_push(app: Application, chat_id: int) -> None:
    if not app.job_queue:
        raise RuntimeError("JobQueue 不可用，请安装依赖 APScheduler")

    name = _job_name(chat_id)
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()

    app.job_queue.run_daily(
        callback=_daily_push_job,
        time=time(hour=9, minute=0, tzinfo=CN_TZ),
        chat_id=chat_id,
        name=name,
    )


def _schedule_cache_refresh(app: Application) -> None:
    if not app.job_queue:
        return

    name = "content_cache_refresh"
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()

    app.job_queue.run_repeating(
        callback=_cache_refresh_job,
        interval=30 * 60,
        first=2,
        name=name,
    )


async def _cache_refresh_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        modules = list_modules()
        if modules:
            refresh_content_cache(modules=modules, limit=10)
    except Exception:
        logger.exception("定时缓存刷新失败")


async def _daily_push_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    try:
        subscriptions = _load_subscriptions()
        cfg = subscriptions.get(chat_id)
        if not cfg or not cfg.get("enabled", False):
            return

        modules = _normalize_modules(cfg.get("modules"))
        if not modules:
            modules = _default_modules()
        if not modules:
            return

        picked_module = random.choice(modules)
        report = get_report(modules=[picked_module], limit=3)
        for part in _split_message(report):
            await context.bot.send_message(chat_id=chat_id, text=part)
    except Exception as exc:
        logger.exception("自动推送失败(chat_id=%s): %s", chat_id, exc)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ 自动推送失败\n错误类型：{type(exc).__name__}\n错误详情：{exc}",
        )


async def autopush(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    subscriptions = _load_subscriptions()
    current = subscriptions.get(
        chat_id,
        {"enabled": False, "modules": _default_modules()},
    )

    action = (context.args[0].strip().lower() if context.args else "status")

    if action in {"on", "start", "enable", "1"}:
        modules = _normalize_modules(",".join(context.args[1:])) if len(context.args) > 1 else _normalize_modules(current.get("modules"))
        if not modules:
            modules = _default_modules()

        subscriptions[chat_id] = {"enabled": True, "modules": modules}
        _schedule_daily_push(context.application, chat_id)
        _save_subscriptions(subscriptions)
        await update.message.reply_text(
            "✅ 已开启自动推送：每天 09:00（北京时间）\n"
            f"订阅板块：{', '.join(modules)}"
        )
        return

    if action in {"off", "stop", "disable", "0"}:
        if context.application.job_queue:
            for job in context.application.job_queue.get_jobs_by_name(_job_name(chat_id)):
                job.schedule_removal()
        subscriptions[chat_id] = {
            "enabled": False,
            "modules": _normalize_modules(current.get("modules")) or _default_modules(),
        }
        _save_subscriptions(subscriptions)
        await update.message.reply_text("🛑 已关闭自动推送")
        return

    if action in {"modules", "module", "m"}:
        modules = _normalize_modules(",".join(context.args[1:])) if len(context.args) > 1 else []
        if not modules:
            available = list_modules()
            await update.message.reply_text(f"❌ 请指定至少一个有效模块。当前可用：{', '.join(available) if available else '暂无'}")
            return

        subscriptions[chat_id] = {
            "enabled": bool(current.get("enabled", False)),
            "modules": modules,
        }
        if subscriptions[chat_id]["enabled"]:
            _schedule_daily_push(context.application, chat_id)
        _save_subscriptions(subscriptions)
        await update.message.reply_text(f"✅ 已更新订阅板块：{', '.join(modules)}")
        return

    status = "已开启" if current.get("enabled", False) else "未开启"
    modules = _normalize_modules(current.get("modules")) or _default_modules()
    available = list_modules()
    await update.message.reply_text(
        "用法：\n"
        "/autopush on [模块1,模块2]\n"
        "/autopush off\n"
        "/autopush modules 模块1,模块2\n"
        "/send [模块1,模块2]\n"
        "/rss <rss_url> [名称]\n"
        "/help\n"
        f"当前可用模块：{', '.join(available) if available else '暂无'}\n"
        f"当前状态：{status}\n"
        f"当前订阅板块：{', '.join(modules)}"
    )


async def send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    subscriptions = _load_subscriptions()
    current = subscriptions.get(chat_id, {"enabled": False, "modules": _default_modules()})

    modules = (
        _normalize_modules(",".join(context.args))
        if context.args
        else _normalize_modules(current.get("modules"))
    )
    if not modules:
        modules = _default_modules()
    if not modules:
        await update.message.reply_text("❌ 当前没有可用模块，请先使用 /rss 添加信息源")
        return

    try:
        selected_modules = modules if context.args else [random.choice(modules)]
        await update.message.reply_text(f"正在推送板块：{', '.join(selected_modules)}，请稍候…")
        report = get_report(modules=selected_modules, limit=3)
        for part in _split_message(report):
            await update.message.reply_text(part)
    except Exception as exc:
        logger.exception("手动推送失败(chat_id=%s): %s", chat_id, exc)
        await update.message.reply_text(
            f"❌ /send 执行失败\n错误类型：{type(exc).__name__}\n错误详情：{exc}"
        )


async def rss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text("用法：/rss <rss_url> [source_name]")
        return

    url = context.args[0].strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        await update.message.reply_text("❌ RSS 地址必须是 http/https 链接")
        return

    source_name = " ".join(context.args[1:]).strip() if len(context.args) > 1 else ""

    try:
        result = add_rss_source(url=url, source_name=source_name)
        refresh_content_cache(modules=[result["module"]], limit=5)
    except Exception as exc:
        logger.exception("添加 RSS 源失败: %s", exc)
        await update.message.reply_text(
            f"❌ 添加 RSS 失败\n错误类型：{type(exc).__name__}\n错误详情：{exc}"
        )
        return

    action_text = "已更新" if result.get("updated") else "已新增"
    await update.message.reply_text(
        f"✅ {action_text} RSS 源\n"
        f"模块：{result.get('module')}（{result.get('module_title')}）\n"
        f"来源：{result.get('source_name')}\n"
        f"URL：{result.get('source_url')}"
    )


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    status_message = await update.message.reply_text("⏳ 已收到，正在规则匹配信息源…")

    registry = _get_registry(context.application)

    matched_modules = match_modules_by_rules(user_text, max_count=3, min_score=2)
    if matched_modules:
        decision = {
            "mode": "tool_call",
            "tool_name": "get_semantic_articles",
            "arguments": {"query": user_text, "top_k": 5, "min_similarity": 0.25},
            "reply": "",
        }
        try:
            await status_message.edit_text(
                f"✅ 已命中规则源：{', '.join(matched_modules)}，正在拉取内容…"
            )
        except Exception:
            pass
    else:
        try:
            await status_message.edit_text("⏳ 规则未命中，正在使用小模型判断调用路径…")
        except Exception:
            pass

        try:
            decision = plan_tool_call_small_model(user_text, registry.list_schemas())
        except Exception:
            logger.exception("小模型工具决策失败，使用规则降级")
            decision = _fallback_tool_decision(user_text)

    if decision.get("mode") == "tool_call":
        tool_name = str(decision.get("tool_name") or "")
        arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
        try:
            try:
                await status_message.edit_text("⏳ 正在请求信息源并生成结果，请稍候…")
            except Exception:
                pass
            tool_result = registry.execute(tool_name, arguments)
        except Exception as exc:
            logger.exception("工具调用失败: %s", exc)
            try:
                await status_message.edit_text("❌ 请求处理失败")
            except Exception:
                pass
            await update.message.reply_text(
                f"❌ 请求处理失败\n错误类型：{type(exc).__name__}\n错误详情：{exc}"
            )
            return

        try:
            try:
                await status_message.edit_text("⏳ 正在整理回复内容…")
            except Exception:
                pass
            reply = generate_user_reply(user_text, tool_result)
        except Exception:
            logger.exception("LLM 回复生成失败，回退原始工具结果")
            reply = tool_result

        try:
            await status_message.edit_text("✅ 已完成，正在分段发送…")
        except Exception:
            pass

        for part in _split_message(reply):
            await update.message.reply_text(part)
        return

    reply = str(decision.get("reply") or "我可以按你的问题自动匹配信息源并推送内容。")
    try:
        await status_message.edit_text("✅ 已完成")
    except Exception:
        pass
    for part in _split_message(reply):
        await update.message.reply_text(part)


async def _post_init(app: Application) -> None:
    if not app.job_queue:
        logger.warning("JobQueue 不可用，自动推送功能不可用。请安装 APScheduler")
        return

    _schedule_cache_refresh(app)
    try:
        modules = list_modules()
        if modules:
            refresh_content_cache(modules=modules, limit=10)
    except Exception:
        logger.exception("启动时缓存预热失败")

    for chat_id, cfg in _load_subscriptions().items():
        if not cfg.get("enabled", False):
            continue
        try:
            _schedule_daily_push(app, chat_id)
        except Exception:
            logger.exception("恢复自动推送失败(chat_id=%s)", chat_id)


def main() -> None:
    token = get_required_env("TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data["mcp_registry"] = create_default_registry()

    app.add_handler(CommandHandler("autopush", autopush))
    app.add_handler(CommandHandler("send", send))
    app.add_handler(CommandHandler("rss", rss))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logger.info("Bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
