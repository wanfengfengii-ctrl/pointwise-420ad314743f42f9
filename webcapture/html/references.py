from dataclasses import dataclass
from html import unescape
from webcapture.html.document import HtmlDocument


@dataclass(frozen=True)
class ImageOccurrence:
    ordinal: int
    start: int
    end: int
    source: str
    token: str


def find_image_occurrences(document: HtmlDocument) -> tuple[ImageOccurrence, ...]:
    result = []
    for tag in document.tags:
        if tag.name != "img":
            continue
        attributes = [attr for attr in tag.attributes if attr.name == "src"]
        if not attributes:
            continue
        if len(attributes) != 1:
            raise ValueError("duplicate img src attributes")
        attribute = attributes[0]
        if attribute.raw_value is None:
            raise ValueError("img src requires a value")
        source = unescape(attribute.raw_value)
        if not source:
            raise ValueError("empty img src")
        result.append(ImageOccurrence(len(result), attribute.start, attribute.end,
                                      source, document.decoded.text[attribute.start:attribute.end]))
    return tuple(result)
