import re

# Telegram bot tokens look like: 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ1234567
TOKEN_PATTERN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{35}\b")


def extract_tokens(text: str) -> list[str]:
    """Pull every bot token out of arbitrary text (a .txt/.json/.csv dump,
    with or without usernames/bot names mixed in on the same lines)."""
    return list(dict.fromkeys(TOKEN_PATTERN.findall(text)))  # de-duped, order kept
