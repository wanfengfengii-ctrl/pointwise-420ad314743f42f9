from typing import Protocol
from webcapture.sources.requests import HttpRequest, build_request
from webcapture.sources.responses import HttpResponse, validate_response


class HttpPort(Protocol):
    def request(self, request: HttpRequest) -> HttpResponse:
        ...


def fetch_response(port: HttpPort, url: str) -> HttpResponse:
    request = build_request(url)
    response = port.request(request)
    return validate_response(request, response)
