"""
Checks for the match chat word and link filters (app/services/chat_filters.py). No database or
network needed:

    uv run python -m scripts.test_chat_filters
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.chat import _message_out
from app.services.chat_filters import check_links, check_spam, check_words

ALLOWED = [
    "gg we are in the lobby",
    "join https://discord.gg/abc123",
    "check twitch.tv/usync",
    "see https://www.youtube.com/watch?v=x",
    "e.g. at 5pm.",
    "my team is Sussex Esports",
    "map pack: steamcommunity.com/id/foo",
    "meet at 7:30pm.",
]

BAD_WORDS = ["what the fuck", "f u c k you", "f.u.c.k"]

BAD_LINKS = [
    "https://pornhub.com/x",
    "go to bit.ly/abc",
    "http://192.168.1.5/x",
    "http://discord.com@evil.com",
    "discord-nitro-free.com",
    "https://xn--discrd-j1a.com",
    "https://cdn.site.com/setup.exe",
    "javascript:alert(1)",
    "grabify.link/abc",
    "steamcommunlty.com/x",
    "cutegirls.xxx",
    "http://[::1]/",
]

for text in ALLOWED:
    assert check_words(text) is None, f"words blocked: {text}"
    assert check_links(text) is None, f"links blocked: {text}"

for text in BAD_WORDS:
    assert check_words(text), f"words let through: {text}"

for text in BAD_LINKS:
    assert check_links(text), f"links let through: {text}"

# Spam shape checks
assert check_spam("see a.com/x b.com/y c.com/z") , "three links let through"
assert check_spam("aaaaaaaaaaaaaaa"), "held-down key let through"
assert check_spam("see https://twitch.tv/usync and https://discord.gg/x") is None

# A hidden message's text only reaches admins who joined; the others get a placeholder.
msg = SimpleNamespace(id = 7, kind = "user", sender_name = "cat", sender_role = "player", sender_account_id = None,
                      body = "something awful", created_at = datetime.now(timezone.utc),
                      hidden_at = datetime.now(timezone.utc), hidden_by_name = "ModMike")
for_players = _message_out(msg, "u1", privileged = False, can_hide = False, reported = set())
for_admins = _message_out(msg, "u2", privileged = True, can_hide = True, reported = set())
assert for_players["body"] == "" and for_players["hidden"] and not for_players["canReport"] and for_players["hiddenBy"] is None
assert for_admins["body"] == "something awful" and for_admins["hiddenBy"] == "ModMike" and not for_admins["canHide"]
admin_msg = SimpleNamespace(**{**msg.__dict__, "sender_role": "admin", "hidden_at": None})
assert not _message_out(admin_msg, "u2", privileged = True, can_hide = True, reported = set())["canHide"], "admins can hide each other"

print(f"chat filters ok: {len(ALLOWED)} allowed, {len(BAD_WORDS)} bad words, {len(BAD_LINKS)} bad links")
