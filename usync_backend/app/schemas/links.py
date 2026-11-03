import re

from typing import Literal

# Socials, competitive site accounts (CMG, Gankster, FACEIT, Battlefy) and the brand links hosts
# and venues show (their website and other pages).
LinkPlatform = Literal[
    "twitch", "twitter", "youtube", "kick", "discord", "instagram",
    "cmg", "gankster", "faceit", "battlefy",
    "website", "tiktok", "linkedin", "facebook", "linktree",
]

_SOCIALS = {"twitch", "twitter", "youtube", "kick", "discord", "instagram"}

# Which platforms each profile type can link: players add competitive sites, hosts and venues
# (organisations) add their own website and brand pages. Matches the frontend's platformsFor.
ALLOWED_LINKS = {
    "player": _SOCIALS | {"cmg", "gankster", "faceit", "battlefy"},
    "host": _SOCIALS | {"website", "tiktok", "linkedin", "facebook", "linktree"},
    "venue": _SOCIALS | {"website", "tiktok", "linkedin", "facebook", "linktree"},
}

def check_link_types(profile_type: str, links: dict[str, str] | None) -> None:
    """
    Raises ValueError if `links` has a platform this profile type can't link.
    """

    not_allowed = sorted(set(links or {}) - ALLOWED_LINKS[profile_type])
    if not_allowed:
        raise ValueError(f"A {profile_type} profile can't link {', '.join(not_allowed)}.")

# Only a handle is ever stored; the frontend builds the link from its own base URL for the
# platform (https://x.com/<handle>, ...). So whatever was typed or pasted - "@name", "x.com/name",
# "https://x.com/name?s=21", even "https://othersite.com/name" - is reduced to "name", and a link
# can never point somewhere else. The one exception is `website`, which is the user's own domain.
# Mirrors toHandle in the frontend's utils/linkedPlatforms.js.

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(r"^(www\.)?[a-z0-9-]+\.[a-z]{2,}$", re.IGNORECASE)
_WEBSITE_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", re.IGNORECASE)
_HANDLE_JUNK_RE = re.compile(r"[^\w.\-]")
_DISCORD_INVITE_RE = re.compile(r"(discord\.gg|discord(app)?\.com/invite)/", re.IGNORECASE)

# LinkedIn pages live under a section ("company/usync", "in/jake"), which is kept with the name.
_LINKEDIN_SECTIONS = {"company", "in", "school", "showcase"}

def _path_segments(value: str) -> list[str]:
    """
    Path segments of whatever was typed or pasted, without the scheme, a leading domain, the
    query string or the #fragment.
    """

    parts = [p for p in re.split(r"[?#]", _SCHEME_RE.sub("", value))[0].split("/") if p]

    # A first segment with a dot is a domain ("x.com"), unless it's all there is - a handle on
    # its own can contain dots ("jake.b" on Instagram).
    if len(parts) > 1 and "." in parts[0]:
        parts.pop(0)
    elif len(parts) == 1 and _BARE_DOMAIN_RE.match(parts[0]) and (_SCHEME_RE.match(value) or "/" in value):
        parts.pop(0)

    return parts

def _clean(value: str) -> str:
    return _HANDLE_JUNK_RE.sub("", value.lstrip("@"))

def normalize_link(platform: str, value: str) -> str:
    """
    The handle to store for `value` on `platform`, or "" if there isn't one in it.
    """

    raw = value.strip()
    if not raw:
        return ""

    if platform == "website":
        site = re.split(r"[?#]", _SCHEME_RE.sub("", raw))[0].rstrip("/")
        return site.lower() if _WEBSITE_RE.match(site) else ""

    if platform == "discord":
        # Players save a username; hosts and venues their server's invite code (the frontend
        # links it as discord.gg/<code>). So an invite link is reduced to its code, and any other
        # URL is rejected rather than turned into a handle.
        if _DISCORD_INVITE_RE.search(raw):
            segments = [s for s in _path_segments(raw) if s.lower() != "invite"]
            return _clean(segments[0]) if segments else ""
        return "" if re.search(r"[/:]", raw) else _clean(raw)

    segments = _path_segments(raw)
    if not segments:
        return ""

    if platform == "linkedin":
        for section, name in zip(segments, segments[1:]):
            if section.lower() in _LINKEDIN_SECTIONS and _clean(name):
                return f"{section.lower()}/{_clean(name)}"
        # Just a name: hosts and venues are organisations, so assume a company page.
        name = _clean(segments[-1])
        return f"company/{name}" if name else ""

    # YouTube and TikTok put the handle after an "@" ("youtube.com/@name/videos"); prefer that
    # segment, otherwise YouTube's older "c/name" and "user/name" pages.
    at = next((s for s in segments if s.startswith("@")), None)
    if at:
        return _clean(at)
    if platform == "youtube" and segments[0] in ("c", "user") and len(segments) > 1:
        return _clean(segments[1])

    return _clean(segments[0])

def normalize_links(links: dict[str, str] | None) -> dict[str, str] | None:
    """
    AfterValidator for a profile's `links`: every handle reduced by normalize_link. A value with
    no handle in it is rejected rather than silently dropped.
    """

    if links is None:
        return None

    cleaned = {}
    for platform, value in links.items():
        handle = normalize_link(platform, value)
        if not handle:
            raise ValueError(f"{value!r} isn't a valid {platform} link or handle.")
        cleaned[platform] = handle

    return cleaned
