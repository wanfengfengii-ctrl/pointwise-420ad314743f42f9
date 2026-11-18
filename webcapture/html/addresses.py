from dataclasses import dataclass
from urllib.parse import urljoin, urldefrag
from webcapture.sources.requests import normalize_request_url


@dataclass(frozen=True)
class ResolvedAddress:
    request_url: str
    fragment: str


ResolvedImageAddress = ResolvedAddress


def resolve_resource_address(final_base_url: str, source: str) -> ResolvedAddress:
    """Resolve one supported reference against a document's final response URL.

    The request identity never carries a fragment; the fragment is preserved
    separately for delivery.
    """
    normalize_request_url(final_base_url)
    if not isinstance(source, str) or not source:
        raise ValueError("empty resource address")
    if any(ord(c) <= 32 or ord(c) == 127 for c in source):
        raise ValueError("resource address contains whitespace or controls")
    absolute = urljoin(final_base_url, source)
    request_url, fragment = urldefrag(absolute)
    return ResolvedAddress(normalize_request_url(request_url), fragment)


def resolve_image_address(final_html_url: str, source: str) -> ResolvedImageAddress:
    return resolve_resource_address(final_html_url, source)
