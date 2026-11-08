"""
Checks for the match chat word and link filters (app/services/chat_filters.py). No database or
network needed:

    uv run python -m scripts.test_chat_filters
"""

from app.services.chat_filters import check_links, check_words

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

print(f"chat filters ok: {len(ALLOWED)} allowed, {len(BAD_WORDS)} bad words, {len(BAD_LINKS)} bad links")
