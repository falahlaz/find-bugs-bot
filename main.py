import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler

import config
from bot.handler import help_command, status_command, history_command, handle_message, make_env_command
from queue.job_queue import job_queue
from scraper.browser import browser_manager
from scraper.vpn_check import is_vpn_connected
from scraper.splunk_scraper import scrape_splunk
from analyzer.llm_analyzer import analyze, LLMAnalysisError
from bot.formatter import format_engineer_report, format_qa_report
from storage.database import save_investigation

shutting_down = False


def setup_logging():
    os.makedirs(config.LOG_DIR, exist_ok=True)

    log_format = "[%(asctime)s] [%(levelname)s] [%(module)s] %(message)s"
    formatter = logging.Formatter(log_format)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("openai").setLevel(logging.WARNING)


async def process_job(job: dict, bot):
    transaction_id = job["transaction_id"]
    requester_chat_id = job["requester_chat_id"]
    environment = job.get("environment", "prod")
    logging.info("Starting job for transaction_id=%s environment=%s", transaction_id, environment)

    try:
        vpn_ok = await is_vpn_connected()
        if not vpn_ok:
            job["vpn_retries"] += 1
            if job["vpn_retries"] < 3:
                logging.warning(
                    "VPN down for transaction_id=%s environment=%s (retry %d/3), re-queuing in 60s",
                    transaction_id, environment, job["vpn_retries"],
                )
                await bot.send_message(
                    chat_id=config.TELEGRAM_YOUR_CHAT_ID,
                    text=f"⚠️ VPN not connected. Job for `{transaction_id}` [{environment}] paused, retrying in 60s.",
                    parse_mode="Markdown",
                )
                await asyncio.sleep(60)
                await job_queue.enqueue(job)
                return
            else:
                logging.error("VPN down after 3 retries for transaction_id=%s, abandoning", transaction_id)
                await bot.send_message(
                    chat_id=config.TELEGRAM_YOUR_CHAT_ID,
                    text=f"❌ Job for `{transaction_id}` [{environment}] abandoned after 3 VPN retries.",
                    parse_mode="Markdown",
                )
                await bot.send_message(
                    chat_id=requester_chat_id,
                    text=f"❌ Investigation failed for `{transaction_id}` — VPN connectivity issue. Please resubmit later.",
                    parse_mode="Markdown",
                )
                await save_investigation(
                    transaction_id=transaction_id,
                    requester_chat_id=requester_chat_id,
                    environment=environment,
                    status="failed",
                    failure_reason="VPN unreachable after 3 retries",
                )
                return

        result_status, log_data = await scrape_splunk(transaction_id, environment)

        if result_status == "session_expired":
            logging.error("Splunk session expired, pausing queue")
            await bot.send_message(
                chat_id=config.TELEGRAM_YOUR_CHAT_ID,
                text="🔐 Splunk session expired. Run `python save_session.py` to renew.",
                parse_mode="Markdown",
            )
            await bot.send_message(
                chat_id=requester_chat_id,
                text=f"⏸️ Investigation paused for `{transaction_id}` — will resume shortly.",
                parse_mode="Markdown",
            )
            job_queue.pause()
            await save_investigation(
                transaction_id=transaction_id,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="failed",
                failure_reason="Splunk session expired",
            )
            return

        if result_status == "browser_restarted":
            logging.warning("Browser restarted, retrying job for transaction_id=%s", transaction_id)
            result_status, log_data = await scrape_splunk(transaction_id, environment)

        if result_status == "browser_error" or result_status == "error":
            logging.error("Browser unrecoverable for transaction_id=%s", transaction_id)
            await bot.send_message(
                chat_id=config.TELEGRAM_YOUR_CHAT_ID,
                text="🚨 Playwright browser crashed and could not recover. Restart the bot.",
                parse_mode="Markdown",
            )
            await bot.send_message(
                chat_id=requester_chat_id,
                text=f"❌ Investigation failed for `{transaction_id}` — technical issue on our end.",
                parse_mode="Markdown",
            )
            await save_investigation(
                transaction_id=transaction_id,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="failed",
                failure_reason="Browser crash, unrecoverable",
            )
            return

        if result_status == "no_logs":
            logging.info("No logs found for transaction_id=%s environment=%s", transaction_id, environment)
            eng_msgs = format_engineer_report(
                transaction_id=transaction_id,
                diagnosis=None,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="no_logs",
            )
            qa_msgs = format_qa_report(
                transaction_id=transaction_id,
                diagnosis=None,
                environment=environment,
                status="no_logs",
            )
            for msg in eng_msgs:
                await bot.send_message(chat_id=config.TELEGRAM_YOUR_CHAT_ID, text=msg, parse_mode="Markdown")
            for msg in qa_msgs:
                await bot.send_message(chat_id=requester_chat_id, text=msg, parse_mode="Markdown")
            await save_investigation(
                transaction_id=transaction_id,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="no_logs",
                raw_log_snippet=log_data,
            )
            return

        if result_status not in ("success", "browser_restarted"):
            logging.error("Unexpected scraper status: %s for transaction_id=%s", result_status, transaction_id)
            await bot.send_message(
                chat_id=config.TELEGRAM_YOUR_CHAT_ID,
                text=f"🚨 Unexpected error processing `{transaction_id}` [{environment}]. Status: {result_status}",
                parse_mode="Markdown",
            )
            await save_investigation(
                transaction_id=transaction_id,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="failed",
                failure_reason=f"Unexpected scraper status: {result_status}",
            )
            return

        diagnosis = None
        llm_failed = False
        llm_raw_text = None

        try:
            diagnosis = await analyze(transaction_id, log_data)
            logging.info("LLM analysis complete for transaction_id=%s environment=%s", transaction_id, environment)
        except LLMAnalysisError as e:
            llm_failed = True
            if "Malformed JSON" in str(e):
                llm_raw_text = str(e).replace("Malformed JSON response: ", "")
            else:
                llm_raw_text = str(e)
            logging.error("LLM analysis failed for transaction_id=%s: %s", transaction_id, e)

        eng_msgs = format_engineer_report(
            transaction_id=transaction_id,
            diagnosis=diagnosis,
            requester_chat_id=requester_chat_id,
            environment=environment,
            raw_log_snippet=log_data,
            llm_failed=llm_failed,
            llm_raw_text=llm_raw_text,
            status="success" if not llm_failed else "failed",
        )
        qa_msgs = format_qa_report(
            transaction_id=transaction_id,
            diagnosis=diagnosis,
            environment=environment,
            status="success" if not llm_failed else "failed",
            llm_failed=llm_failed,
        )

        for msg in eng_msgs:
            await bot.send_message(chat_id=config.TELEGRAM_YOUR_CHAT_ID, text=msg, parse_mode="Markdown")
        for msg in qa_msgs:
            await bot.send_message(chat_id=requester_chat_id, text=msg, parse_mode="Markdown")

        await save_investigation(
            transaction_id=transaction_id,
            requester_chat_id=requester_chat_id,
            environment=environment,
            status="success" if not llm_failed else "failed",
            error_type=diagnosis.get("error_type") if diagnosis else None,
            failed_component=diagnosis.get("failed_component") if diagnosis else None,
            severity=diagnosis.get("severity") if diagnosis else None,
            summary=diagnosis.get("summary") if diagnosis else None,
            likely_cause=diagnosis.get("likely_cause") if diagnosis else None,
            suggested_action=diagnosis.get("suggested_action") if diagnosis else None,
            raw_log_snippet=log_data,
            failure_reason="LLM analysis failed" if llm_failed else None,
        )

    except Exception:
        logging.exception("Unhandled error processing job for transaction_id=%s", transaction_id)
        try:
            await bot.send_message(
                chat_id=config.TELEGRAM_YOUR_CHAT_ID,
                text=f"🚨 Unexpected error processing `{transaction_id}` [{environment}]. Check logs/bot.log for details.",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def post_init(application):
    from storage.database import init_db
    init_db()

    try:
        await browser_manager.start()
    except Exception as e:
        if "not found" in str(e).lower() or "no such file" in str(e).lower():
            logging.critical("splunk_session.json not found. Run 'python save_session.py' first.")
            sys.exit(1)
        raise

    job_queue.initialize(application.bot)
    await job_queue.start_worker(process_job)
    application.bot_data["job_queue"] = job_queue
    application.bot_data["shutting_down"] = False

    logging.info("Bot started. Queue is empty.")


async def post_shutdown(application):
    await browser_manager.close()


def main():
    if not os.path.exists(config.SPLUNK_SESSION_PATH):
        print(f"❌ {config.SPLUNK_SESSION_PATH} not found. Run 'python save_session.py' first.")
        sys.exit(1)

    setup_logging()
    logging.info("Starting Telegram Debug Bot...")

    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", help_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))

    for env_key in config.SPLUNK_ENVIRONMENTS:
        application.add_handler(CommandHandler(env_key, make_env_command(env_key)))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    import signal as sig
    loop = asyncio.new_event_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        try:
            loop.add_signal_handler(s, lambda sn=s: _async_shutdown(application, sn))
        except RuntimeError:
            pass

    logging.info("Starting polling...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


async def _async_shutdown(app, signum):
    global shutting_down
    shutting_down = True
    logging.info("Initiating graceful shutdown (signal %s)...", signum)

    app.bot_data["shutting_down"] = True

    pending = job_queue.queue_depth
    if job_queue.current_job:
        pending += 1

    if pending > 0:
        try:
            await app.bot.send_message(
                chat_id=config.TELEGRAM_YOUR_CHAT_ID,
                text=f"🛑 Bot is shutting down. Draining {pending} remaining job(s)...",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        drained = await job_queue.drain()
        if not drained:
            logging.warning("Drain timed out, some jobs may not have completed")
    else:
        logging.info("No pending jobs, shutting down immediately.")

    await browser_manager.close()
    logging.info("Shutdown complete.")
    import os
    os._exit(0)


if __name__ == "__main__":
    main()