from telegram.error import BadRequest


async def send_message_safe(bot, chat_id: int, text: str, parse_mode: str = "Markdown", **kwargs):
    """Send with Markdown, falling back to plain text when Telegram rejects the markup."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, **kwargs)
    except BadRequest:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
