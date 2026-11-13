import re
import logging
import unicodedata
import ipaddress

from urllib.parse import urlsplit
from better_profanity import profanity
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Match chat moderation. Three layers, all applied before a message is stored:
#
#   1. Banned words - the same better_profanity list the usernames use (app.services.users),
#      checked on the normalized text, a split-up version (camelCase, digits, punctuation) and a
#      "squashed" version so "f u c k" and "f.u.c.k" don't slip through.
#   2. Links - allowed, but not to sites that are X-rated or risky for the people clicking them
#      (IP loggers, URL shorteners that hide the destination, phishing look-alikes of Discord, Steam
#      and friends, raw IPs, punycode look-alikes, direct downloads of programs).
#   3. OpenAI moderation - the same model the usernames use, for harassment, hate and sexual
#      content. Plain game-talk violence ("I'll destroy you") is let through on purpose.
#
# A message that fails any layer is rejected with a 422 and is never stored.

MAX_MESSAGE_LENGTH = 500

profanity.load_censor_words()

# ---- links -------------------------------------------------------------------

# Hosts we never allow. A host matches when it IS the entry or ends with ".<entry>".
ADULT_DOMAINS = {
    "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "redtube.com", "youporn.com", "tube8.com",
    "spankbang.com", "eporner.com", "txxx.com", "beeg.com", "brazzers.com", "bangbros.com", "naughtyamerica.com",
    "realitykings.com", "chaturbate.com", "stripchat.com", "bongacams.com", "cam4.com", "myfreecams.com",
    "livejasmin.com", "camsoda.com", "onlyfans.com", "fansly.com", "manyvids.com", "e-hentai.org", "nhentai.net",
    "hentaihaven.xxx", "rule34.xxx", "rule34.us", "e621.net", "gelbooru.com", "danbooru.donmai.us", "literotica.com",
    "motherless.com", "4tube.com", "porntrex.com", "hqporner.com", "thothub.to", "erome.com", "coomer.su", "kemono.su",
    "pornhat.com", "sunporno.com", "tnaflix.com", "fapello.com", "xxxbunker.com", "adultfriendfinder.com",
}

# IP loggers, grabbers and known malware or phishing hosts.
MALICIOUS_DOMAINS = {
    "grabify.link", "iplogger.org", "iplogger.com", "iplogger.ru", "ipgrabber.ru", "2no.co", "yip.su", "ip.sb.to",
    "leancoding.co", "stopify.co", "freegiftcards.co", "joinmy.site", "curiouscat.club", "gyazo.nl", "blasze.com",
    "blasze.tk", "spottyfly.com", "bmwforum.co", "linkbucks.com", "adf.ly", "sh.st", "ouo.io", "bc.vc",
    "shorte.st", "exe.io", "clk.sh", "link-to.net", "discord-nitro.gift", "discordgift.site", "dlscord.com",
}

# Shorteners hide where a link really goes, which is how most chat scams work.
SHORTENER_DOMAINS = {
    "bit.ly", "bitly.com", "tinyurl.com", "goo.gl", "is.gd", "v.gd", "ow.ly", "cutt.ly", "rb.gy", "rebrand.ly",
    "shorturl.at", "tiny.cc", "buff.ly", "bl.ink", "t.ly", "lnkd.in", "shorturl.com", "tr.ee", "short.io",
    "qr.ae", "s.id", "urlz.fr", "snip.ly", "soo.gd", "clck.ru", "vk.cc", "kutt.it", "shrtco.de", "x.co",
}

# Anything containing these in the host is treated as adult. (Not "sex": too many real words.)
ADULT_HOST_WORDS = ("porn", "xxx", "hentai", "nsfw", "camgirl", "sexcam", "erotic", "xvideo", "onlyfan", "nudes", "fetish")
ADULT_TLDS = {"xxx", "porn", "sex", "sexy", "adult", "cam", "dating"}

# Brand names scammers imitate, and the hosts that really belong to them. A host that mentions
# the name but isn't one of the real ones (discord-gift.com, steamcommunlty.com) is blocked.
IMPERSONATED = {
    "discord": {"discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media", "discord.new", "discord.gift", "discordstatus.com"},
    "steam": {"steampowered.com", "steamcommunity.com", "steamstatic.com", "steamcdn-a.akamaihd.net", "steamgames.com"},
    "twitch": {"twitch.tv", "twitchcdn.net", "twitchapps.com", "jtvnw.net"},
    "battle.net": {"battle.net", "blizzard.com"},
    "activision": {"activision.com", "callofduty.com"},
    "paypal": {"paypal.com", "paypal.me"},
    "roblox": {"roblox.com"},
    "xbox": {"xbox.com", "xboxlive.com"},
    "playstation": {"playstation.com", "playstation.net"},
    "usync": {"usync.gg"},
}

# Programs and scripts are never something to share in a match chat.
DANGEROUS_EXTENSIONS = (
    ".exe", ".scr", ".bat", ".cmd", ".com", ".msi", ".msix", ".apk", ".jar", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".ps1", ".psm1", ".lnk", ".hta", ".dll", ".sh", ".dmg", ".pkg", ".iso", ".reg", ".cpl", ".appimage",
)

URL_RE = re.compile(
    r"""(?ix)
    (?<![\w@.-])
    (
        (?:https?://|ftp://|www\.)[^\s<>"'`]+
        |
        (?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}(?::\d{2,5})?(?:/[^\s<>"'`]*)?
    )
    """
)
SCHEME_RE = re.compile(r"(?i)\b(?:javascript|vbscript|file|blob|data)\s*:\s*(?://|[a-z]+/|\S)")
TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"

def _matches(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)

def _host_of(raw: str) -> tuple[str, str, bool]:
    """
    Splits a found link into (host, path, had_scheme). A bare "example.com/x" is parsed as if it had
    a scheme so the host comes out right.
    """

    raw = raw.rstrip(TRAILING_PUNCTUATION)
    had_scheme = "://" in raw
    parts = urlsplit(raw if had_scheme else "http://" + raw)
    if "@" in parts.netloc:
        # http://discord.com@evil.com - the part before the @ is what the reader sees.
        return "user@info", parts.path, had_scheme
    return (parts.hostname or "").lower().rstrip("."), parts.path.lower(), had_scheme

def _check_host(host: str, path: str, had_scheme: bool) -> str | None:
    """
    The reason a link is blocked, or None when it's fine.
    """

    if not host:
        return None
    if host == "user@info":
        return "Links with a username in the address aren't allowed."

    try:
        ipaddress.ip_address(host.strip("[]"))
        return "Links to raw IP addresses aren't allowed."
    except ValueError:
        pass

    labels = host.split(".")
    if any(label.startswith("xn--") for label in labels):
        return "Links with look-alike (punycode) addresses aren't allowed."

    if _matches(host, ADULT_DOMAINS) or labels[-1] in ADULT_TLDS or any(w in host for w in ADULT_HOST_WORDS):
        return "Links to adult sites aren't allowed."
    if _matches(host, MALICIOUS_DOMAINS):
        return "That link is blocked because it's known to be unsafe."
    if _matches(host, SHORTENER_DOMAINS):
        return "Shortened links aren't allowed. Paste the full address instead."

    for name, real in IMPERSONATED.items():
        if name in host.replace("-", "") and not _matches(host, real):
            return "That link looks like it's pretending to be another site."

    if had_scheme and path.endswith(DANGEROUS_EXTENSIONS):
        return "Links to downloadable programs aren't allowed."
    return None

def check_links(text: str) -> str | None:
    """
    The reason a message's links are blocked, or None. Links themselves are allowed; only the
    sites above (and unsafe schemes) are not.
    """

    if SCHEME_RE.search(text):
        return "That kind of link isn't allowed."
    for found in URL_RE.finditer(text):
        reason = _check_host(*_host_of(found.group(1)))
        if reason:
            return reason
    return None

# ---- banned words --------------------------------------------------------------

_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_NOT_ALLOWED = "That message contains language that isn't allowed. Please reword it."

def _normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text.strip())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")

def _squash(text: str) -> str:
    """
    "f u c k" -> "fuck": runs of single characters separated by spaces or punctuation are joined.
    """

    tokens = re.split(r"[\s._*\-|]+", text)
    out, run = [], []
    for token in tokens:
        if len(token) == 1:
            run.append(token)
            continue
        if len(run) > 1:
            out.append("".join(run))
        else:
            out.extend(run)
        run = []
        out.append(token)
    out.extend(["".join(run)] if len(run) > 1 else run)
    return " ".join(out)

def check_words(text: str) -> str | None:
    """
    The same banned-word check the usernames use, over a few forms of the text.
    """

    normalized = _normalize(text)
    split = _SPLIT_RE.sub(" ", re.sub(r"[_./-]+", " ", normalized))
    for candidate in (normalized, split, _squash(normalized)):
        if profanity.contains_profanity(candidate):
            return _NOT_ALLOWED
    return None

# ---- spam ----------------------------------------------------------------------

MAX_LINKS_PER_MESSAGE = 2
_REPEAT_RE = re.compile(r"(.)\1{9,}")

def check_spam(text: str) -> str | None:
    """
    Shape checks for spammy messages: too many links, or a character held down ("aaaaaaaaaaaa").
    How often someone sends is limited separately, in app.services.chat.
    """

    if len(URL_RE.findall(text)) > MAX_LINKS_PER_MESSAGE:
        return f"Please keep it to {MAX_LINKS_PER_MESSAGE} links per message."
    if _REPEAT_RE.search(text):
        return "That message looks like spam. Please reword it."
    return None

# ---- OpenAI --------------------------------------------------------------------

# Categories worth rejecting a chat line for. Plain "violence" is left out: it's how people
# talk about games ("I'm going to destroy you").
BLOCKED_CATEGORIES = {
    "harassment", "harassment_threatening", "hate", "hate_threatening", "sexual", "sexual_minors",
    "self_harm_intent", "self_harm_instructions", "violence_graphic",
}

async def check_moderation(text: str, openai: AsyncOpenAI) -> str | None:
    """
    OpenAI's moderation model, like the usernames. If the service is down the message goes through
    (the word and link checks already ran) rather than chat going dark.
    """

    try:
        response = await openai.with_options(timeout = 4.0, max_retries = 0).moderations.create(
            model = "omni-moderation-latest",
            input = text,
        )
    except Exception:
        logger.warning("Chat moderation unavailable; allowing the message.", exc_info = True)
        return None

    categories = response.results[0].categories.model_dump()
    if any(categories.get(name) for name in BLOCKED_CATEGORIES):
        return _NOT_ALLOWED
    return None

async def screen_message(text: str, openai: AsyncOpenAI | None = None) -> str | None:
    """
    The reason a message can't be sent, or None when it's clean. Cheap checks run first so most
    rejections never reach OpenAI.
    """

    reason = check_spam(text) or check_words(text) or check_links(text)
    if reason or openai is None:
        return reason
    return await check_moderation(text, openai)
