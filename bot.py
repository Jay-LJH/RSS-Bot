import logging
import json
from pathlib import Path
from datetime import time, timezone, timedelta
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import get_required_env
from crawler import get_report, get_today_report

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

SUBSCRIPTIONS_FILE = Path(__file__).resolve().parent / "subscriptions.json"
CN_TZ = timezone(timedelta(hours=8))
DEFAULT_MODULES = ["new", "tech", "trend"]
VALID_MODULES = set(DEFAULT_MODULES)


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

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("正在抓取日报并生成 AI 总结，请稍候…")
        report = get_today_report(limit=3)
        for part in _split_message(report):
            await update.message.reply_text(part)
    except Exception as exc:
        logger.exception("抓取 Trending 失败: %s", exc)
        error_text = f"❌ /today 执行失败\n错误类型：{type(exc).__name__}\n错误详情：{exc}"
        await update.message.reply_text(error_text)


def _normalize_modules(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return DEFAULT_MODULES.copy()

    if isinstance(raw, list):
        parts = [str(x).strip().lower() for x in raw]
    else:
        text = raw.replace("，", ",").replace("|", ",")
        parts = [x.strip().lower() for x in text.split(",") if x.strip()]

    normalized: list[str] = []
    for item in parts:
        key = "new" if item == "news" else item
        if key in VALID_MODULES and key not in normalized:
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
                    "modules": DEFAULT_MODULES.copy(),
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
                    modules = DEFAULT_MODULES.copy()
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
                "modules": _normalize_modules(cfg.get("modules")) or DEFAULT_MODULES.copy(),
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


async def _daily_push_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    try:
        subscriptions = _load_subscriptions()
        cfg = subscriptions.get(chat_id)
        if not cfg or not cfg.get("enabled", False):
            return

        modules = _normalize_modules(cfg.get("modules"))
        report = get_report(modules=modules, limit=3)
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
        {"enabled": False, "modules": DEFAULT_MODULES.copy()},
    )

    action = (context.args[0].strip().lower() if context.args else "status")

    if action in {"on", "start", "enable", "1"}:
        modules = _normalize_modules(",".join(context.args[1:])) if len(context.args) > 1 else _normalize_modules(current.get("modules"))
        if not modules:
            modules = DEFAULT_MODULES.copy()

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
            "modules": _normalize_modules(current.get("modules")) or DEFAULT_MODULES.copy(),
        }
        _save_subscriptions(subscriptions)
        await update.message.reply_text("🛑 已关闭自动推送")
        return

    if action in {"modules", "module", "m"}:
        modules = _normalize_modules(",".join(context.args[1:])) if len(context.args) > 1 else []
        if not modules:
            await update.message.reply_text("❌ 请指定至少一个模块：new, tech, trend")
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
    modules = _normalize_modules(current.get("modules")) or DEFAULT_MODULES.copy()
    await update.message.reply_text(
        "用法：\n"
        "/autopush on [new,tech,trend]\n"
        "/autopush off\n"
        "/autopush modules new,tech\n"
        "/send [new,tech,trend]\n"
        f"当前状态：{status}\n"
        f"当前订阅板块：{', '.join(modules)}"
    )


async def send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    subscriptions = _load_subscriptions()
    current = subscriptions.get(chat_id, {"enabled": False, "modules": DEFAULT_MODULES.copy()})

    modules = (
        _normalize_modules(",".join(context.args))
        if context.args
        else _normalize_modules(current.get("modules"))
    )
    if not modules:
        modules = DEFAULT_MODULES.copy()

    try:
        await update.message.reply_text(f"正在推送板块：{', '.join(modules)}，请稍候…")
        report = get_report(modules=modules, limit=3)
        for part in _split_message(report):
            await update.message.reply_text(part)
    except Exception as exc:
        logger.exception("手动推送失败(chat_id=%s): %s", chat_id, exc)
        await update.message.reply_text(
            f"❌ /send 执行失败\n错误类型：{type(exc).__name__}\n错误详情：{exc}"
        )


async def _post_init(app: Application) -> None:
    if not app.job_queue:
        logger.warning("JobQueue 不可用，自动推送功能不可用。请安装 APScheduler")
        return

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

    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("autopush", autopush))
    app.add_handler(CommandHandler("send", send))

    logger.info("Bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
