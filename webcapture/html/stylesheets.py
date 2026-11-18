"""Extraction of the finite V2 style vocabulary from an HTML document.

The functions here only interpret one already-decoded HTML document.  They
never fetch anything and never resolve addresses; that belongs to the capture
graph, which owns request identities and response bases.
"""

from dataclasses import dataclass
from html import unescape
import re
from webcapture.html.css import scan_css
from webcapture.html.document import HtmlAttribute, HtmlDocument, HtmlTag
from webcapture.html.entities import decode_with_map
from webcapture.html.references import find_image_occurrences

_SPACE = " \t\r\n\f"
_SPLIT = re.compile(r"[ \t\r\n\f]+")

CONTEXT_ATTRIBUTE = "attribute"
CONTEXT_STYLE_ATTRIBUTE = "style_attribute"
CONTEXT_STYLE_TEXT = "style_text"
CONTEXT_CSS_TEXT = "css_text"


@dataclass(frozen=True)
class HtmlStyleReference:
    kind: str  # image | link | url | import
    context: str
    start: int
    end: int
    token: str
    source: str


def _raw_offset(attribute: HtmlAttribute) -> int:
    return attribute.start + (1 if attribute.quote else 0)


def _mapped_span(spans, start: int, end: int) -> tuple[int, int]:
    """Map a decoded token range to one replaceable raw span.

    Character references expand to several decoded characters that share a
    raw span.  A token boundary cutting such a group would delete part of a
    reference, so that is rejected rather than guessed at.
    """
    if not 0 <= start < end <= len(spans):
        raise ValueError("inline CSS token outside the attribute value")
    low, high, continuation = spans[start]
    if continuation:
        raise ValueError("inline CSS token starts inside a character reference")
    position = start + 1
    while position < end:
        piece = spans[position]
        if piece[0] != high:
            raise ValueError("inline CSS token is not contiguous")
        high = piece[1]
        position += 1
    if position < len(spans) and spans[position][0] == low and spans[position][1] == high:
        raise ValueError("inline CSS token ends inside a character reference")
    return low, high


def _inline_references(text: str, absolute: int, spans=None) -> list[HtmlStyleReference]:
    references = []
    for token in scan_css(text):
        if spans is None:
            start, end = absolute + token.start, absolute + token.end
        else:
            low, high = _mapped_span(spans, token.start, token.end)
            start, end = absolute + low, absolute + high
        references.append(HtmlStyleReference(
            "url" if token.kind == "url" else "import", CONTEXT_STYLE_ATTRIBUTE,
            start, end, token.raw, token.decoded))
    return references


def _style_attribute(tag: HtmlTag) -> HtmlAttribute | None:
    attributes = [attribute for attribute in tag.attributes if attribute.name == "style"]
    if not attributes:
        return None
    if len(attributes) != 1:
        raise ValueError("duplicate style attributes")
    if attributes[0].raw_value is None:
        raise ValueError("style attribute requires a value")
    return attributes[0]


def _stylesheet_href(tag: HtmlTag) -> HtmlAttribute | None:
    rels = [attribute for attribute in tag.attributes if attribute.name == "rel"]
    if not rels:
        return None
    tokens = set()
    for rel in rels:
        if rel.raw_value is not None:
            tokens.update(part.lower() for part in _SPLIT.split(unescape(rel.raw_value)) if part)
    if "stylesheet" not in tokens:
        return None
    if len(rels) != 1:
        raise ValueError("duplicate link rel attributes on a stylesheet link")
    hrefs = [attribute for attribute in tag.attributes if attribute.name == "href"]
    if len(hrefs) != 1:
        raise ValueError("stylesheet link requires exactly one href attribute")
    href = hrefs[0]
    if href.raw_value is None or not unescape(href.raw_value):
        raise ValueError("stylesheet link requires a nonempty href")
    return href


def find_style_references(document: HtmlDocument) -> tuple[HtmlStyleReference, ...]:
    """Return every supported reference in document order.

    Positions are half-open Unicode character intervals into the decoded
    document text.  ``token`` is the raw span; ``source`` is the decoded
    target after HTML character-reference processing (and CSS escapes for
    inline CSS).
    """
    result = []
    for occurrence in find_image_occurrences(document):
        result.append(HtmlStyleReference("image", CONTEXT_ATTRIBUTE,
                                         occurrence.start, occurrence.end,
                                         occurrence.token, occurrence.source))
    for tag in document.tags:
        if tag.name == "link":
            href = _stylesheet_href(tag)
            if href is not None:
                result.append(HtmlStyleReference(
                    "link", CONTEXT_ATTRIBUTE, href.start, href.end,
                    document.decoded.text[href.start:href.end], unescape(href.raw_value)))
        attribute = _style_attribute(tag)
        if attribute is not None:
            decoded, spans = decode_with_map(attribute.raw_value)
            result.extend(_inline_references(decoded, _raw_offset(attribute), spans))
    for block in document.raw_blocks:
        if block.tag != "style":
            continue
        if not block.closed:
            raise ValueError("unterminated style element")
        for token in scan_css(block.content):
            start = block.content_start + token.start
            end = block.content_start + token.end
            result.append(HtmlStyleReference(
                "url" if token.kind == "url" else "import", CONTEXT_STYLE_TEXT,
                start, end, token.raw, token.decoded))
    result.sort(key=lambda reference: reference.start)
    seen_end = -1
    for reference in result:
        if reference.start < seen_end:
            raise ValueError("overlapping supported references in the document")
        seen_end = reference.end
    return tuple(result)
