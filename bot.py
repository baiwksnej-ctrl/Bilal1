# ============================================================
# bot.py - Free Fire Bot (Telegram + Flask + Activator + Likes)
# Full pipeline: Generate → Activate → Like
# ============================================================
import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import urllib3
from flask import Flask, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from jwt_client import FreeFireLogin, enc_aes, UA_UNITY
from like_engine import run_like_batch, get_proxy_count
from activator_engine import activate_batch, PB2_AVAILABLE

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ CONFIG ============
BOT_TOKEN = os.environ.get("TG_TOKEN", "8776921304:AAE75XN-ZOXlBzbaikhxBOW9KQcPki2LREU")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7373420615"))
ACCOUNTS_FILE = os.environ.get("ACCOUNTS_FILE", "accounts.json")
TARGET_FILE = os.environ.get("TARGET_FILE", "target.json")
PORT = int(os.environ.get("PORT", 8080))
WORKERS = int(os.environ.get("LIKE_WORKERS", "3"))
ACT_CONCURRENT = int(os.environ.get("ACT_CONCURRENT", "20"))
RESUME = os.environ.get("RESUME", "1") == "1"
PROGRESS_EVERY_SEC = 6

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("ff_bot")

# ============ STATE ============
is_running = False
is_activating = False
active_chat_id = None
current_target_uid = None
live_progress_msg_id = None
waiting_for_uid_input = False
stats = {"done": 0, "ok": 0, "fail": 0, "total": 0, "start_time": 0.0, "error_breakdown": {}}
act_stats = {"done": 0, "ok": 0, "fail": 0, "total": 0, "start_time": 0.0}
STOP_FLAG = {"stop": False}
LOG_LINES = []
LOG_LOCK = threading.Lock()
_ACCOUNTS_CACHE = {"list": [], "loaded_at": 0}


def log_line(msg):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with LOG_LOCK:
        LOG_LINES.append(line)
        if len(LOG_LINES) > 800:
            del LOG_LINES[:300]


def is_admin(uid):
    try:
        return int(uid) == ADMIN_ID
    except Exception:
        return False


# ============ TARGET ============
def load_target():
    global current_target_uid
    try:
        if os.path.exists(TARGET_FILE):
            with open(TARGET_FILE, "r") as f:
                current_target_uid = str(json.load(f).get("target_uid") or "")
    except Exception:
        pass


def save_target(uid):
    global current_target_uid
    current_target_uid = str(uid)
    try:
        with open(TARGET_FILE, "w") as f:
            json.dump({"target_uid": current_target_uid}, f)
    except Exception:
        pass


load_target()


# ============ ACCOUNTS ============
def load_accounts(force=False):
    now = time.time()
    if not force and _ACCOUNTS_CACHE["list"] and (now - _ACCOUNTS_CACHE["loaded_at"]) < 60:
        return _ACCOUNTS_CACHE["list"]

    out = []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            seen = set()
            for acc in data:
                if isinstance(acc, dict):
                    info = acc.get("guest_account_info") or acc
                    uid = info.get("com.garena.msdk.guest_uid") or info.get("uid")
                    pwd = info.get("com.garena.msdk.guest_password") or info.get("password")
                    if uid and pwd and str(uid) not in seen:
                        seen.add(str(uid))
                        out.append({"uid": str(uid), "password": str(pwd)})
    except Exception as e:
        log.error(f"[accounts] {e}")

    _ACCOUNTS_CACHE["list"] = out
    _ACCOUNTS_CACHE["loaded_at"] = now
    return out


def save_activation_results(results):
    """Update accounts.json with activation data."""
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []

    result_map = {r["uid"]: r for r in results if isinstance(r, dict)}

    for acc in data:
        if isinstance(acc, dict):
            info = acc.get("guest_account_info") or acc
            uid = str(info.get("com.garena.msdk.guest_uid") or info.get("uid") or "")
            if uid in result_map and result_map[uid].get("status") == "success":
                r = result_map[uid]
                acc["activated"] = True
                acc["account_id"] = r["data"].get("account_id", "")
                acc["detected_region"] = r["data"].get("detected_region", "")
                acc["activation_token"] = r["data"].get("token", "")
                acc["activation_time"] = datetime.utcnow().isoformat()

    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error(f"[save_activation] {e}")


# ============ FLASK ============
def run_flask():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return jsonify({
            "status": "ok",
            "running": is_running,
            "activating": is_activating,
            "target_uid": current_target_uid,
            "done": stats["done"], "ok": stats["ok"], "fail": stats["fail"], "total": stats["total"],
        })

    @app.route("/health")
    def health():
        return "ok", 200

    @app.route("/accounts-count")
    def acc():
        return jsonify({"count": len(load_accounts())})

    @app.route("/logs")
    def get_logs():
        with LOG_LOCK:
            return jsonify({"lines": LOG_LINES[-200:]})

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


# ============ KEYBOARDS (ENGLISH) ============
def kb_main():
    like_txt = "🛑 Stop Likes" if is_running else "❤️ Send Likes"
    like_cb = "like_stop" if is_running else "like_start"
    act_txt = "🔄 Activating..." if is_activating else "⚡ Activate All Accounts"
    act_cb = "act_running" if is_activating else "act_start"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(like_txt, callback_data=like_cb)],
        [InlineKeyboardButton(act_txt, callback_data=act_cb)],
        [InlineKeyboardButton("🎯 Set Target UID", callback_data="set_uid"),
         InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("🔑 Test Account", callback_data="test_acc"),
         InlineKeyboardButton("♻️ Reload", callback_data="reload")],
        [InlineKeyboardButton("📥 Download Failed", callback_data="dl_failed"),
         InlineKeyboardButton("📜 Logs", callback_data="logs")],
        [InlineKeyboardButton("🗑️ Clear Logs", callback_data="clear_logs"),
         InlineKeyboardButton("🆔 My ID", callback_data="myid")],
    ])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])


def main_text():
    acc_n = len(load_accounts())
    proxy_n = get_proxy_count()
    tgt = current_target_uid or "not set"
    state = "🟢 Running" if is_running else "🔴 Idle"
    act_state = "🟡 Activating..." if is_activating else "⚪ Idle"
    pb2_state = "✅" if PB2_AVAILABLE else "❌"
    return (
        f"❤️ *Free Fire Bot — v1.0*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Likes: {state}\n"
        f"⚡ Activation: {act_state}\n"
        f"🎯 Target UID: `{tgt}`\n"
        f"👥 Accounts: {acc_n}\n"
        f"🌐 Proxies: {proxy_n}\n"
        f"📦 Pb2 Modules: {pb2_state}\n"
        f"📈 Last Session: {stats['ok']}/{stats['total']}\n"
    )


# ============ ACTIVATION ASYNC ============
async def run_activation_async(chat_id, context):
    global is_activating, act_stats

    accounts = load_accounts()
    if not accounts:
        await context.bot.send_message(chat_id=chat_id, text="❌ No accounts found")
        return

    is_activating = True
    act_stats.update({"done": 0, "ok": 0, "fail": 0, "total": len(accounts), "start_time": time.time()})

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚡ *Activation Started*\n👥 {len(accounts)} accounts\n⚙️ Concurrent: {ACT_CONCURRENT}",
        parse_mode="Markdown",
    )

    # Prepare minimal accounts for activator
    act_input = [
        {"uid": a["uid"], "password": a["password"], "name": f"ACC-{a['uid'][-4:]}", "region": "auto"}
        for a in accounts
    ]

    progress_state = {"last": 0.0, "ok": 0, "fail": 0}

    def on_progress(idx, total, result):
        progress_state["done"] = idx
        if result.get("status") == "success":
            progress_state["ok"] += 1
        else:
            progress_state["fail"] += 1

        now = time.time()
        if now - progress_state["last"] < 4 and idx != total:
            return
        progress_state["last"] = now

        act_stats["done"] = idx
        act_stats["ok"] = progress_state["ok"]
        act_stats["fail"] = progress_state["fail"]

        txt = (
            f"⚡ *Activating...*\n"
            f"📦 {idx}/{total}\n"
            f"✅ {progress_state['ok']}  ❌ {progress_state['fail']}"
        )
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id=chat_id, text=txt, parse_mode="Markdown"),
            asyncio.get_running_loop(),
        )

    try:
        results = await activate_batch(act_input, ACT_CONCURRENT, on_progress)
    except Exception as e:
        log.error(f"[activation] {e}\n{traceback.format_exc()}")
        results = []

    # Normalize results (gather returns may include exceptions)
    valid = []
    for r in results:
        if isinstance(r, dict):
            valid.append(r)

    save_activation_results(valid)

    success = sum(1 for r in valid if r.get("status") == "success")
    partial = sum(1 for r in valid if r.get("status") == "partial")
    failed = sum(1 for r in valid if r.get("status") in ("failed", "error"))

    is_activating = False

    summary = (
        f"🏁 *Activation Complete*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Total: {len(valid)}\n"
        f"✅ Success: {success}\n"
        f"⚠️ Partial: {partial}\n"
        f"❌ Failed: {failed}\n"
    )
    await context.bot.send_message(
        chat_id=chat_id, text=summary,
        reply_markup=kb_main(), parse_mode="Markdown",
    )


# ============ LIKE ASYNC ============
async def run_like_async(chat_id, context):
    global is_running, live_progress_msg_id, stats

    tgt = current_target_uid
    if not tgt:
        await context.bot.send_message(chat_id=chat_id, text="❌ No target UID set")
        return

    accounts = load_accounts()
    if not accounts:
        await context.bot.send_message(chat_id=chat_id, text="❌ No accounts found")
        return

    is_running = True
    STOP_FLAG["stop"] = False
    stats.update({"done": 0, "ok": 0, "fail": 0, "total": len(accounts),
                  "start_time": time.time(), "error_breakdown": {}})
    live_progress_msg_id = None

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🚀 *Likes Started*\n🎯 `{tgt}`\n👥 {len(accounts)} accounts\n⚙️ Workers: {WORKERS}",
        parse_mode="Markdown",
    )

    start_ts = time.time()
    loop = asyncio.get_running_loop()
    last_ts = {"t": 0}

    def on_progress(done, total, ok, fail, res):
        now = time.time()
        if now - last_ts["t"] < PROGRESS_EVERY_SEC and done != total:
            return
        last_ts["t"] = now
        stats["done"], stats["ok"], stats["fail"] = done, ok, fail

        el = now - start_ts
        rate = done / max(el, 1) * 60
        pct = done / total * 100 if total else 0
        bar_n = int(12 * pct / 100)
        bar = "█" * bar_n + "░" * (12 - bar_n)
        eta = (total - done) / max(rate / 60, 0.01) if rate > 0 else 0
        mark = "✅" if res.get("success") else "❌"

        txt = (
            f"❤️ *Sending Likes...*\n`{bar}` {pct:.1f}%\n\n"
            f"📦 {done}/{total}\n"
            f"✅ {ok}   ❌ {fail}\n"
            f"⚡ {rate:.0f}/min   ⏱ {el:.0f}s\n"
            f"⏳ ETA: {eta:.0f}s\n"
            f"Last: {mark} `…{str(res.get('uid',''))[-6:]}`"
        )
        asyncio.run_coroutine_threadsafe(_update_progress(context, chat_id, txt), loop)

    try:
        ok, fail, results = await asyncio.to_thread(
            run_like_batch, accounts, tgt, WORKERS,
            on_progress, STOP_FLAG, RESUME,
        )
    except Exception as e:
        log.error(f"[like] {e}\n{traceback.format_exc()}")
        ok, fail, results = 0, len(accounts), []

    el = time.time() - start_ts
    breakdown = {}
    for r in results:
        if not r.get("success"):
            err = r.get("error", "unknown")
            breakdown[err] = breakdown.get(err, 0) + 1
    stats["error_breakdown"] = breakdown
    is_running = False

    header = "🛑 *Stopped*" if STOP_FLAG["stop"] else "🏁 *Completed*"
    summary = (
        f"{header}\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Target: `{tgt}`\n"
        f"✅ Success: {ok}\n"
        f"❌ Failed: {fail}\n"
        f"⏱ {el:.0f}s\n"
    )
    if breakdown:
        summary += "\n*Errors:*\n"
        for err, cnt in sorted(breakdown.items(), key=lambda x: -x[1])[:5]:
            summary += f"  • `{err[:30]}`: {cnt}\n"

    await context.bot.send_message(
        chat_id=chat_id, text=summary,
        reply_markup=kb_main(), parse_mode="Markdown",
    )


async def _update_progress(context, chat_id, txt):
    global live_progress_msg_id
    try:
        if live_progress_msg_id is None:
            m = await context.bot.send_message(chat_id=chat_id, text=txt, parse_mode="Markdown")
            live_progress_msg_id = m.message_id
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=live_progress_msg_id,
                text=txt, parse_mode="Markdown",
            )
    except Exception as e:
        if "not modified" not in str(e):
            log.error(f"[progress] {e}")


# ============ HANDLERS ============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_chat_id
    active_chat_id = update.effective_chat.id
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text(f"🚫 Unauthorized: `{uid}`", parse_mode="Markdown")
        return
    await update.message.reply_text(main_text(), reply_markup=kb_main(), parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global waiting_for_uid_input, is_running, is_activating, active_chat_id

    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    active_chat_id = chat_id

    if not is_admin(uid):
        await q.answer("🚫 Unauthorized", show_alert=True)
        return

    data = q.data

    if data == "main_menu":
        await q.edit_message_text(main_text(), reply_markup=kb_main(), parse_mode="Markdown")

    elif data == "set_uid":
        waiting_for_uid_input = True
        await q.edit_message_text(
            "🎯 *Send Target UID*\nType the UID in chat.\nExample: `7895804990`",
            reply_markup=kb_back(), parse_mode="Markdown",
        )

    elif data == "like_start":
        if is_running:
            await q.answer("⚠️ Already running", show_alert=True); return
        if not current_target_uid:
            await q.edit_message_text(
                "⚠️ *No target UID set*",
                reply_markup=kb_main(), parse_mode="Markdown",
            ); return
        await q.edit_message_text(
            f"🚀 *Starting Likes*\n🎯 `{current_target_uid}`",
            reply_markup=kb_main(), parse_mode="Markdown",
        )
        asyncio.create_task(run_like_async(chat_id, context))

    elif data == "like_stop":
        STOP_FLAG["stop"] = True
        await q.edit_message_text("🛑 *Stop requested*", reply_markup=kb_main(), parse_mode="Markdown")

    elif data == "act_start":
        if is_activating:
            await q.answer("⚠️ Already activating", show_alert=True); return
        await q.edit_message_text(
            "⚡ *Starting Activation...*",
            reply_markup=kb_main(), parse_mode="Markdown",
        )
        asyncio.create_task(run_activation_async(chat_id, context))

    elif data == "act_running":
        await q.answer("⚠️ Activation in progress", show_alert=True)

    elif data == "status":
        el = time.time() - stats["start_time"] if stats["start_time"] else 0
        txt = (
            "📊 *Status*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Likes running: `{is_running}`\n"
            f"⚡ Activating: `{is_activating}`\n"
            f"🎯 Target UID: `{current_target_uid or '—'}`\n"
            f"✅ Success: {stats['ok']}\n"
            f"❌ Failed: {stats['fail']}\n"
            f"📦 Done: {stats['done']}/{stats['total']}\n"
        )
        if el:
            txt += f"⏱ Elapsed: {el:.0f}s\n"
        if stats["error_breakdown"]:
            txt += "\n*Errors:*\n"
            for err, cnt in sorted(stats["error_breakdown"].items(), key=lambda x: -x[1])[:5]:
                txt += f"  • `{err[:30]}`: {cnt}\n"
        await q.edit_message_text(txt, reply_markup=kb_back(), parse_mode="Markdown")

    elif data == "test_acc":
        accs = load_accounts()
        if not accs:
            await q.edit_message_text("❌ No accounts", reply_markup=kb_main()); return
        sample = accs[0]
        await q.edit_message_text(f"🔑 *Testing*\nuid: `{sample['uid']}`", parse_mode="Markdown")
        try:
            t0 = time.time()
            engine = FreeFireLogin()
            res = await asyncio.to_thread(engine.login, sample["uid"], sample["password"])
            dt = time.time() - t0
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ *Success*\naccount_id: `{res['account_id']}`\n⏱ {dt:.2f}s",
                parse_mode="Markdown",
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Failed: `{str(e)[:80]}`",
                parse_mode="Markdown",
            )
        await context.bot.send_message(chat_id=chat_id, text="🔙", reply_markup=kb_main())

    elif data == "reload":
        n = len(load_accounts(force=True))
        await q.edit_message_text(f"♻️ Loaded {n} accounts", reply_markup=kb_main())

    elif data == "dl_failed":
        tgt = current_target_uid
        fname = f"failed_accounts_{tgt}.json" if tgt else None
        if fname and os.path.exists(fname):
            with open(fname, "rb") as f:
                content = f.read()
            await context.bot.send_document(
                chat_id=chat_id, document=content,
                filename=fname, caption=f"📥 Failed ({tgt})",
            )
        else:
            await q.edit_message_text("📭 No failed file", reply_markup=kb_main())

    elif data == "logs":
        with LOG_LOCK:
            lines = LOG_LINES[-60:]
        txt = "\n".join(lines) if lines else "📭 No logs"
        for i in range(0, len(txt), 3500):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"```\n{txt[i:i+3500]}\n```",
                parse_mode="Markdown",
            )
        await context.bot.send_message(chat_id=chat_id, text="🔙", reply_markup=kb_main())

    elif data == "clear_logs":
        with LOG_LOCK:
            LOG_LINES.clear()
        await q.edit_message_text("🗑️ Cleared", reply_markup=kb_main())

    elif data == "myid":
        bot_u = (await context.bot.get_me()).username
        await q.edit_message_text(
            f"🆔 Your ID: `{uid}`\n🤖 @{bot_u}",
            reply_markup=kb_main(), parse_mode="Markdown",
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global waiting_for_uid_input, active_chat_id
    uid = update.effective_user.id
    active_chat_id = update.effective_chat.id
    if not is_admin(uid):
        return

    if waiting_for_uid_input:
        txt = update.message.text.strip()
        if not txt.isdigit():
            await update.message.reply_text("❌ UID must be numeric", reply_markup=kb_main())
            return
        waiting_for_uid_input = False
        save_target(txt)
        await update.message.reply_text(
            f"✅ *Target UID set*\n`{txt}`\n\nPress '❤️ Send Likes' to start.",
            reply_markup=kb_main(), parse_mode="Markdown",
        )


# ============ MAIN ============
async def main_async():
    threading.Thread(target=run_flask, daemon=True).start()
    log_line(f"[main] Flask on {PORT}")
    log_line("=== FF BOT START ===")
    log_line(f"admin={ADMIN_ID} workers={WORKERS} act_concurrent={ACT_CONCURRENT}")
    log_line(f"accounts loaded: {len(load_accounts(force=True))}")
    log_line(f"target={current_target_uid or 'none'}")
    log_line(f"pb2 available: {PB2_AVAILABLE}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    log_line("Initializing bot...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    log_line("Bot polling started (ALL_TYPES)")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            pass


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log_line("[main] stopped by user")


if __name__ == "__main__":
    main()