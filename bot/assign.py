"""QA → developer assignment of finished bug reports.

The QA summary carries an inline keyboard listing every developer by name. QA
toggles one or more names and presses "Send report"; only then does the full
engineer report reach those developers.

Selection state lives in the keyboard labels (⬜/✅) and the assignment history
lives in the `assigned_to` DB column — nothing is kept in memory, so the buttons
keep working after a bot restart.
"""

import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import config
from bot.formatter import _now_formatted, _split_message
from bot.messaging import send_message_safe
from storage.database import add_assignees, get_by_id

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "asg"
CALLBACK_PATTERN = r"^asg:"

UNCHECKED = "⬜"
CHECKED = "✅"
ASSIGNED = "✔️"

DEVELOPERS_PER_ROW = 2


def build_assign_keyboard(
    investigation_id: int,
    assigned: set[int] = frozenset(),
    selected: set[int] = frozenset(),
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for chat_id, name in config.TELEGRAM_DEVELOPERS.items():
        if chat_id in assigned:
            mark = ASSIGNED
        elif chat_id in selected:
            mark = CHECKED
        else:
            mark = UNCHECKED
        row.append(
            InlineKeyboardButton(
                f"{mark} {name}",
                callback_data=f"{CALLBACK_PREFIX}:{investigation_id}:{chat_id}",
            )
        )
        if len(row) == DEVELOPERS_PER_ROW:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            f"📤 Send report ({len(selected)})",
            callback_data=f"{CALLBACK_PREFIX}:{investigation_id}:send",
        ),
        InlineKeyboardButton(
            "✖️ Skip",
            callback_data=f"{CALLBACK_PREFIX}:{investigation_id}:skip",
        ),
    ])

    return InlineKeyboardMarkup(rows)


def _selected_from_markup(markup: InlineKeyboardMarkup | None) -> set[int]:
    """Read the ✅-marked developers back out of the keyboard we last rendered."""
    selected: set[int] = set()
    if markup is None:
        return selected

    for row in markup.inline_keyboard:
        for button in row:
            if not button.callback_data or not button.text.startswith(CHECKED):
                continue
            try:
                selected.add(int(button.callback_data.rsplit(":", 1)[1]))
            except (IndexError, ValueError):
                continue
    return selected


def _assigned_from_row(row: dict) -> set[int]:
    try:
        return set(json.loads(row.get("assigned_to") or "[]"))
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupt assigned_to for investigation id=%s", row.get("id"))
        return set()


def _names(chat_ids) -> str:
    return ", ".join(config.chat_name(cid) for cid in chat_ids)


async def _edit_keyboard(query, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def handle_assign_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return

    chat_id = query.message.chat.id
    if chat_id not in config.TELEGRAM_ALLOWED_CHAT_IDS and chat_id != config.TELEGRAM_YOUR_CHAT_ID:
        await query.answer("⛔ Not authorized.", show_alert=True)
        return

    try:
        _, raw_id, action = (query.data or "").split(":", 2)
        investigation_id = int(raw_id)
    except ValueError:
        await query.answer()
        return

    row = await get_by_id(investigation_id)
    if row is None:
        await query.answer("This report is no longer available.", show_alert=True)
        return

    if chat_id != row["requester_chat_id"] and chat_id != config.TELEGRAM_YOUR_CHAT_ID:
        await query.answer("⛔ Only the reporter can assign this report.", show_alert=True)
        return

    assigned = _assigned_from_row(row)
    selected = _selected_from_markup(query.message.reply_markup)

    if action == "skip":
        await _edit_keyboard(query, None)
        await query.answer("Report not assigned.")
        logger.info("Investigation id=%d skipped by chat_id=%d", investigation_id, chat_id)
        return

    if action == "send":
        await _send_to_developers(query, context, investigation_id, row, assigned, selected)
        return

    try:
        developer_id = int(action)
    except ValueError:
        await query.answer()
        return

    if developer_id not in config.TELEGRAM_DEVELOPERS:
        await query.answer("That developer is no longer on the roster.", show_alert=True)
        return

    if developer_id in assigned:
        await query.answer(f"{config.chat_name(developer_id)} is already assigned.")
        return

    selected.symmetric_difference_update({developer_id})
    await _edit_keyboard(query, build_assign_keyboard(investigation_id, assigned, selected))
    await query.answer()


async def _send_to_developers(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    investigation_id: int,
    row: dict,
    assigned: set[int],
    selected: set[int],
) -> None:
    targets = [cid for cid in config.TELEGRAM_DEVELOPERS if cid in selected and cid not in assigned]
    if not targets:
        await query.answer("Pick at least one developer first.", show_alert=True)
        return

    try:
        messages = json.loads(row.get("engineer_report") or "[]")
    except json.JSONDecodeError:
        messages = []

    if not messages:
        await query.answer("The report content is missing — check the bot logs.", show_alert=True)
        logger.error("Investigation id=%d has no engineer_report to assign", investigation_id)
        return

    qa_chat_id = query.message.chat.id
    footer = (
        f"\n\n👤 Assigned by: {config.chat_name(qa_chat_id)}\n"
        f"🕐 Assigned at: {_now_formatted()}"
    )
    chunks = messages[:-1] + _split_message(messages[-1] + footer)

    delivered: list[int] = []
    failed: list[int] = []
    for developer_id in targets:
        try:
            for chunk in chunks:
                await send_message_safe(context.bot, developer_id, text=chunk)
            delivered.append(developer_id)
        except Exception:
            logger.exception(
                "Failed to deliver investigation id=%d to developer chat_id=%d",
                investigation_id, developer_id,
            )
            failed.append(developer_id)

    if delivered:
        assigned = set(await add_assignees(investigation_id, delivered))
        logger.info(
            "Investigation id=%d assigned to %s by chat_id=%d",
            investigation_id, delivered, qa_chat_id,
        )

    await _edit_keyboard(query, build_assign_keyboard(investigation_id, assigned))

    lines = []
    if delivered:
        lines.append(f"📤 Report sent to: {_names(delivered)}")
    for developer_id in failed:
        lines.append(
            f"⚠️ Could not reach {config.chat_name(developer_id)} — "
            "they need to open the bot and press /start first."
        )

    await query.answer("Report sent." if delivered else "Delivery failed.", show_alert=not delivered)
    await send_message_safe(context.bot, qa_chat_id, text="\n".join(lines))
