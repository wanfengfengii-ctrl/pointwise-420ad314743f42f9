from dataclasses import dataclass
from urllib.parse import urldefrag, urlsplit


def normalize_request_url(url: str) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("URL must be a nonempty string")
    if any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise ValueError("URL contains whitespace, controls or backslashes")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise ValueError("absolute HTTP(S) URL required")
        if not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("URL host required; credentials are unsupported")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError("invalid port")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid HTTP(S) URL") from exc
    return urldefrag(url)[0]


@dataclass(frozen=True)
class HttpRequest:
    url: str

    def __post_init__(self):
        if normalize_request_url(self.url) != self.url:
            raise ValueError("request identity must have no fragment")


def build_request(url: str) -> HttpRequest:
    return HttpRequest(normalize_request_url(url))
