from dataclasses import dataclass
from webcapture.sources.requests import HttpRequest, normalize_request_url


class ResponseError(ValueError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    request_url: str
    final_url: str
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


def validate_response(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    if not isinstance(response, HttpResponse):
        raise ResponseError("HTTP port returned a non-response")
    if response.request_url != request.url:
        raise ResponseError("response request identity mismatch")
    try:
        normalize_request_url(response.final_url)
    except ValueError as exc:
        raise ResponseError("invalid final response URL") from exc
    if type(response.status) is not int or not 200 <= response.status < 300:
        raise ResponseError(f"unsuccessful HTTP status: {response.status!r}")
    if not isinstance(response.body, bytes):
        raise ResponseError("response body must be bytes")
    if not isinstance(response.headers, tuple):
        raise ResponseError("headers must be an ordered tuple")
    for pair in response.headers:
        if (not isinstance(pair, tuple) or len(pair) != 2
                or any(not isinstance(value, str) for value in pair)):
            raise ResponseError("invalid header pair")
        if any("\r" in value or "\n" in value for value in pair):
            raise ResponseError("header contains a line break")
    return response
