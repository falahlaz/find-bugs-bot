import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, MessageHandler, filters, CommandHandler

import config
from bot.handler import help_command, status_command, history_command, myid_command, handle_message, make_env_command, make_time_range_command
from jobqueue.job_queue import job_queue
from scraper.splunk_api import splunk_api
from scraper.vpn_check import is_vpn_connected
from scraper.splunk_scraper import scrape_splunk
from analyzer.llm_analyzer import analyze, LLMAnalysisError, close_client
from bot.formatter import format_engineer_report, format_qa_report
from storage.database import save_investigation

shutting_down = False


async def send_message_safe(bot, chat_id: int, text: str, parse_mode: str = "Markdown", **kwargs):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, **kwargs)
    except BadRequest:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)


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
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("openai").setLevel(logging.WARNING)


async def process_job(job: dict, bot):
    transaction_id = job["transaction_id"]
    requester_chat_id = job["requester_chat_id"]
    environment = job.get("environment", "prod")
    time_range = job.get("time_range", config.SPLUNK_DEFAULT_TIME_RANGE)
    logging.info("Starting job for transaction_id=%s environment=%s time_range=%s", transaction_id, environment, time_range)

    try:
        vpn_ok = await is_vpn_connected()
        if not vpn_ok:
            job["vpn_retries"] += 1
            if job["vpn_retries"] < 3:
                logging.warning(
                    "VPN down for transaction_id=%s environment=%s (retry %d/3), re-queuing in 60s",
                    transaction_id, environment, job["vpn_retries"],
                )
                await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID,
                    text=f"⚠️ VPN not connected. Job for `{transaction_id}` [{environment}] paused, retrying in 60s.",
                )
                await asyncio.sleep(60)
                await job_queue.enqueue(job)
                return
            else:
                logging.error("VPN down after 3 retries for transaction_id=%s, abandoning", transaction_id)
                await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID,
                    text=f"❌ Job for `{transaction_id}` [{environment}] abandoned after 3 VPN retries.",
                )
                await send_message_safe(bot, requester_chat_id,
                    text=f"❌ Investigation failed for `{transaction_id}` — VPN connectivity issue. Please resubmit later.",
                )
                await save_investigation(
                    transaction_id=transaction_id,
                    requester_chat_id=requester_chat_id,
                    environment=environment,
                    status="failed",
                    failure_reason="VPN unreachable after 3 retries",
                )
                return

        result_status, log_data = await scrape_splunk(transaction_id, environment, time_range)

        if result_status == "session_expired":
            logging.error("Splunk session expired, attempting auto re-auth")
            reauth_ok = await splunk_api.auto_reauth()
            if reauth_ok:
                logging.info("Re-auth succeeded, re-queuing job for transaction_id=%s", transaction_id)
                await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID,
                    text=f"🔐 Session expired — auto re-auth successful. Resuming job for `{transaction_id}`...",
                )
                job_queue.resume()
                await job_queue.enqueue(job)
                return
            else:
                logging.error("Auto re-auth failed for transaction_id=%s", transaction_id)
                await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID,
                    text="🔐 Splunk session expired. Auto re-auth failed — run `python save_session_auto.py` to renew.",
                )
                job_queue.pause()
                await send_message_safe(bot, requester_chat_id,
                    text=f"⏸️ Investigation paused for `{transaction_id}` — will resume shortly.",
                )
                await save_investigation(
                    transaction_id=transaction_id,
                    requester_chat_id=requester_chat_id,
                    environment=environment,
                    status="failed",
                    failure_reason="Splunk session expired (auto re-auth failed)",
                )
                return

        if result_status == "error":
            logging.error("Splunk API error for transaction_id=%s", transaction_id)
            await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID,
                text=f"🚨 Splunk API error processing `{transaction_id}` [{environment}]. Check logs/bot.log for details.",
            )
            await save_investigation(
                transaction_id=transaction_id,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="failed",
                failure_reason=f"Splunk API error",
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
                time_range=time_range,
            )
            qa_msgs = format_qa_report(
                transaction_id=transaction_id,
                diagnosis=None,
                environment=environment,
                status="no_logs",
                time_range=time_range,
            )
            for msg in eng_msgs:
                await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID, text=msg)
            for msg in qa_msgs:
                await send_message_safe(bot, requester_chat_id, text=msg)
            await save_investigation(
                transaction_id=transaction_id,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="no_logs",
                raw_log_snippet=log_data,
                time_range=time_range,
            )
            return

        if result_status not in ("success",):
            logging.error("Unexpected scraper status: %s for transaction_id=%s", result_status, transaction_id)
            await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID,
                text=f"🚨 Unexpected error processing `{transaction_id}` [{environment}]. Status: {result_status}",
            )
            await save_investigation(
                transaction_id=transaction_id,
                requester_chat_id=requester_chat_id,
                environment=environment,
                status="failed",
                failure_reason=f"Unexpected scraper status: {result_status}",
                time_range=time_range,
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
            time_range=time_range,
        )
        qa_msgs = format_qa_report(
            transaction_id=transaction_id,
            diagnosis=diagnosis,
            environment=environment,
            status="success" if not llm_failed else "failed",
            llm_failed=llm_failed,
            time_range=time_range,
        )

        for msg in eng_msgs:
            await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID, text=msg)
        for msg in qa_msgs:
            await send_message_safe(bot, requester_chat_id, text=msg)

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
            time_range=time_range,
        )

    except Exception:
        logging.exception("Unhandled error processing job for transaction_id=%s", transaction_id)
        try:
            await send_message_safe(bot, config.TELEGRAM_YOUR_CHAT_ID,
                text=f"🚨 Unexpected error processing `{transaction_id}` [{environment}]. Check logs/bot.log for details.",
            )
        except Exception:
            pass


async def post_init(application):
    from storage.database import init_db
    init_db()

    await splunk_api.start()

    job_queue.initialize(application.bot)
    await job_queue.start_worker(process_job)
    application.bot_data["job_queue"] = job_queue
    application.bot_data["shutting_down"] = False

    logging.info("Bot started. Queue is empty.")


async def post_shutdown(application):
    await job_queue.stop_worker()
    await close_client()
    await splunk_api.close()


def main():
    if not os.path.exists(config.SPLUNK_API_SESSION_PATH):
        if config.SPLUNK_SSO_EMAIL and config.SPLUNK_SSO_EMPLOYEE_ID and config.SPLUNK_SSO_PASSWORD:
            print(f"⚠️  Session not found. Running auto-login...")
            from save_session_auto import auto_login
            success = auto_login()
            if not success:
                print("❌ Auto-login failed. Fix credentials in .env and try again.")
                sys.exit(1)
        else:
            print(f"❌ {config.SPLUNK_API_SESSION_PATH} not found.")
            print("   Set SPLUNK_SSO_EMAIL, SPLUNK_SSO_EMPLOYEE_ID, and SPLUNK_SSO_PASSWORD in .env, then run:")
            print("   python save_session_auto.py")
            sys.exit(1)

    setup_logging()
    logging.info("Starting Telegram Debug Bot (Splunk API mode)...")

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
    application.add_handler(CommandHandler("myid", myid_command))

    for env_key in config.SPLUNK_ENVIRONMENTS:
        application.add_handler(CommandHandler(env_key, make_env_command(env_key)))

    for tr_key in config.SPLUNK_TIME_RANGES:
        application.add_handler(CommandHandler(tr_key, make_time_range_command(tr_key)))

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
            await send_message_safe(app.bot, config.TELEGRAM_YOUR_CHAT_ID,
                text=f"🛑 Bot is shutting down. Draining {pending} remaining job(s)...",
            )
        except Exception:
            pass

        drained = await job_queue.drain()
        if not drained:
            logging.warning("Drain timed out, some jobs may not have completed")
    else:
        logging.info("No pending jobs, shutting down immediately.")

    await job_queue.stop_worker()
    await close_client()
    await splunk_api.close()
    logging.info("Shutdown complete.")
    os._exit(0)


if __name__ == "__main__":
    main()