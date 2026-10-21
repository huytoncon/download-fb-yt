"""Tách và kiểm tra link YouTube / Facebook từ đoạn text người dùng dán vào."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

SOURCE_DOMAINS = {
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "facebook": ("facebook.com", "fb.watch", "fb.com"),
}

_SEPARATORS = re.compile(r"[\s,;]+")


@dataclass(frozen=True)
class ParsedLinks:
    valid: tuple[tuple[str, str], ...]  # (url, source)
    invalid: tuple[str, ...]


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def detect_source(url: str) -> str | None:
    """Trả về 'youtube' / 'facebook' nếu link hợp lệ, ngược lại None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    for source, domains in SOURCE_DOMAINS.items():
        if any(_host_matches(host, d) for d in domains):
            return source
    return None


def _normalize(token: str) -> str:
    token = token.strip().strip("<>\"'()[]")
    if token and "://" not in token:
        token = "https://" + token
    return token


def parse_links(text: str) -> ParsedLinks:
    """Tách nhiều link (mỗi dòng, dấu cách, dấu phẩy...), bỏ trùng, giữ thứ tự."""
    seen: set[str] = set()
    valid: list[tuple[str, str]] = []
    invalid: list[str] = []
    for raw in _SEPARATORS.split(text or ""):
        if not raw.strip():
            continue
        url = _normalize(raw)
        if url in seen:
            continue
        seen.add(url)
        source = detect_source(url)
        if source:
            valid.append((url, source))
        else:
            invalid.append(raw.strip())
    return ParsedLinks(valid=tuple(valid), invalid=tuple(invalid))
