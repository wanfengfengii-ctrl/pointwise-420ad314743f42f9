"""Dependency-graph interpretation shared by style capture and verification.

The graph owns request identities, response bases and roles.  It is driven by
an injected synchronous fetcher, so capture can use the HTTP port while
verification can resolve only fixed evidence and never touch the network.

Interpretation is a depth-first walk: a newly discovered CSS document is
fully interpreted before the referring document continues.  That is the order
the request log records, and it terminates on cycles because a request only
gets the CSS role once.
"""

from webcapture.capture.jobs import CaptureError
from webcapture.html.addresses import resolve_resource_address
from webcapture.html.css import scan_css
from webcapture.html.document import parse_document
from webcapture.html.encoding import decode_css, decode_html
from webcapture.html.stylesheets import CONTEXT_CSS_TEXT, find_style_references

HTML_ROLE = "html"
CSS_ROLE = "css"
BINARY_ROLE = "binary"

HTML_DOCUMENT = "html"
CSS_DOCUMENT_PREFIX = "css:"

_KIND_ROLE = {
    "image": BINARY_ROLE,
    "url": BINARY_ROLE,
    "link": CSS_ROLE,
    "import": CSS_ROLE,
}


def css_document_key(request_url: str) -> str:
    return CSS_DOCUMENT_PREFIX + request_url


def document_resource(document: str) -> str | None:
    """Return the request identity an interpreted CSS document came from."""
    if isinstance(document, str) and document.startswith(CSS_DOCUMENT_PREFIX):
        return document[len(CSS_DOCUMENT_PREFIX):]
    return None


def reference_role(kind: str) -> str:
    try:
        return _KIND_ROLE[kind]
    except KeyError as exc:
        raise CaptureError(f"unsupported reference kind: {kind}") from exc


class StyleGraph:
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.responses = {}
        self.order = []
        self.roles = {}
        self.documents = []
        self.references = {}

    def _fetch(self, request_url: str):
        if request_url not in self.responses:
            response = self.fetcher(request_url)
            self.responses[request_url] = response
            self.order.append(request_url)
            self.roles[request_url] = set()
        return self.responses[request_url]

    def _document_references(self, document: str, request_url: str):
        """Decode one document and return its supported references in order."""
        response = self.responses[request_url]
        result = []
        if document == HTML_DOCUMENT:
            decoded = decode_html(response.body)
            for reference in find_style_references(parse_document(decoded)):
                result.append((reference.kind, reference.context, reference.start,
                               reference.end, reference.token, reference.source))
        else:
            decoded = decode_css(response.body)
            for token in scan_css(decoded.text):
                result.append(("url" if token.kind == "url" else "import",
                               CONTEXT_CSS_TEXT, token.start, token.end,
                               token.raw, token.decoded))
        return result

    def _record(self, document, kind, context, start, end, token, source, address):
        self.references[document].append({
            "document": document, "kind": kind, "context": context,
            "ordinal": len(self.references[document]),
            "start": start, "end": end, "token": token, "source": source,
            "request_url": address.request_url, "fragment": address.fragment,
        })

    def run(self, entry_url: str) -> "StyleGraph":
        self._fetch(entry_url)
        self.roles[entry_url].add(HTML_ROLE)
        self.documents.append(HTML_DOCUMENT)
        self.references[HTML_DOCUMENT] = []
        stack = [[HTML_DOCUMENT, entry_url, 0,
                  self._document_references(HTML_DOCUMENT, entry_url)]]
        while stack:
            frame = stack[-1]
            document, request_url, index, references = frame
            if index >= len(references):
                stack.pop()
                continue
            frame[2] = index + 1
            kind, context, start, end, token, source = references[index]
            address = resolve_resource_address(
                self.responses[request_url].final_url, source)
            role = reference_role(kind)
            self._fetch(address.request_url)
            newly_interpreted = role not in self.roles[address.request_url]
            self.roles[address.request_url].add(role)
            self._record(document, kind, context, start, end, token, source, address)
            if newly_interpreted and role == CSS_ROLE:
                child = css_document_key(address.request_url)
                self.documents.append(child)
                self.references[child] = []
                stack.append([child, address.request_url, 0,
                              self._document_references(child, address.request_url)])
        return self

    @property
    def entry_request_url(self) -> str:
        return self.order[0]

    def roles_for(self, request_url: str) -> tuple[str, ...]:
        order = (HTML_ROLE, CSS_ROLE, BINARY_ROLE)
        return tuple(role for role in order if role in self.roles[request_url])
