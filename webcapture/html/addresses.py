from dataclasses import dataclass
from urllib.parse import urljoin, urldefrag
from webcapture.sources.requests import normalize_request_url


@dataclass(frozen=True)
class ResolvedImageAddress:
    request_url: str
    fragment: str


def resolve_image_address(final_html_url: str, source: str) -> ResolvedImageAddress:
    normalize_request_url(final_html_url)
    if not isinstance(source, str) or not source:
        raise ValueError("empty image address")
    if any(ord(c) <= 32 or ord(c) == 127 for c in source):
        raise ValueError("image address contains whitespace or controls")
    absolute = urljoin(final_html_url, source)
    request_url, fragment = urldefrag(absolute)
    return ResolvedImageAddress(normalize_request_url(request_url), fragment)
