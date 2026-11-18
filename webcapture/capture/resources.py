from dataclasses import dataclass
from webcapture.html.addresses import resolve_image_address
from webcapture.html.references import ImageOccurrence
from webcapture.sources.requests import build_request
from webcapture.sources.responses import HttpResponse, validate_response


@dataclass(frozen=True)
class ImageBinding:
    occurrence: ImageOccurrence
    request_url: str
    fragment: str


class ImageResources:
    def __init__(self, html_response: HttpResponse):
        validate_response(build_request(html_response.request_url), html_response)
        self.html = html_response
        self.responses = {html_response.request_url: html_response}
        self.image_requests = ()
        self._bindings = ()
        self._planned = False

    def plan(self, occurrences: tuple[ImageOccurrence, ...]) -> tuple[str, ...]:
        if self._planned:
            raise ValueError("image plan already fixed")
        bindings = []
        ordered = {}
        for occurrence in occurrences:
            address = resolve_image_address(self.html.final_url, occurrence.source)
            ordered.setdefault(address.request_url, None)
            bindings.append(ImageBinding(occurrence, address.request_url, address.fragment))
        self.image_requests = tuple(ordered)
        self._bindings = tuple(bindings)
        self._planned = True
        return tuple(url for url in ordered if url not in self.responses)

    def record(self, response: HttpResponse) -> None:
        if not self._planned or response.request_url not in self.image_requests:
            raise ValueError("unplanned image response")
        validate_response(build_request(response.request_url), response)
        if response.request_url in self.responses:
            raise ValueError("request already acquired")
        self.responses[response.request_url] = response

    @property
    def bindings(self) -> tuple[ImageBinding, ...]:
        return self._bindings

    @property
    def planned(self) -> bool:
        return self._planned
