from dataclasses import dataclass
from html.parser import HTMLParser
import re
from webcapture.html.encoding import DecodedHtml

_NAME = re.compile(r"[^\s=/>]+")
_SPACE = " \t\r\n\f"
_RAW_CLOSE = re.compile(r"</style(?=[\t\n\r\f />])", re.IGNORECASE)


@dataclass(frozen=True)
class HtmlAttribute:
    name: str
    raw_value: str | None
    start: int
    end: int
    quote: str | None


@dataclass(frozen=True)
class HtmlTag:
    name: str
    start: int
    end: int
    attributes: tuple[HtmlAttribute, ...]


@dataclass(frozen=True)
class RawTextBlock:
    tag: str
    tag_start: int
    tag_end: int
    content_start: int
    content_end: int
    content: str
    closed: bool


@dataclass(frozen=True)
class HtmlDocument:
    decoded: DecodedHtml
    tags: tuple[HtmlTag, ...]
    raw_blocks: tuple[RawTextBlock, ...] = ()


def _attributes(raw: str, absolute: int) -> tuple[HtmlAttribute, ...]:
    match = re.match(r"<[A-Za-z][^\s/>]*", raw)
    if match is None:
        raise ValueError("invalid start tag")
    position = match.end()
    result = []
    while position < len(raw):
        while position < len(raw) and raw[position] in _SPACE:
            position += 1
        if raw[position:] in (">", "/>"):
            return tuple(result)
        name = _NAME.match(raw, position)
        if name is None:
            raise ValueError("malformed HTML attribute")
        attribute_name = name.group().lower()
        position = name.end()
        while position < len(raw) and raw[position] in _SPACE:
            position += 1
        if position >= len(raw) or raw[position] != "=":
            result.append(HtmlAttribute(attribute_name, None,
                                        absolute + position, absolute + position, None))
            continue
        position += 1
        while position < len(raw) and raw[position] in _SPACE:
            position += 1
        start = position
        if position >= len(raw):
            raise ValueError("missing HTML attribute value")
        quote = raw[position] if raw[position] in ("'", '"') else None
        if quote:
            close = raw.find(quote, position + 1)
            if close < 0:
                raise ValueError("unterminated quoted HTML attribute")
            value = raw[position + 1:close]
            position = close + 1
            if position < len(raw) and raw[position] not in _SPACE + "/>":
                raise ValueError("missing attribute separator")
        else:
            while position < len(raw) and raw[position] not in _SPACE + ">":
                if raw[position] in "'\"<\x60":
                    raise ValueError("invalid unquoted HTML attribute")
                position += 1
            value = raw[start:position]
            if not value:
                raise ValueError("missing HTML attribute value")
        result.append(HtmlAttribute(attribute_name, value, absolute + start,
                                    absolute + position, quote))
    raise ValueError("unterminated HTML start tag")


class _Parser(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.line_starts = [0]
        for index, character in enumerate(text):
            if character == "\n":
                self.line_starts.append(index + 1)
        self.tags = []
        self.raw_blocks = []

    def _position(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self._position()
        raw = self.get_starttag_text()
        self.tags.append(HtmlTag(tag.lower(), start, start + len(raw),
                                 _attributes(raw, start)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.cdata_elem != "style":
            return
        content_start = self._position()
        content_end = content_start + len(data)
        if self.text[content_start:content_end] != data:
            raise ValueError("raw style text disagrees with the document")
        close = _RAW_CLOSE.match(self.text, content_end)
        self.raw_blocks.append(RawTextBlock(
            "style", 0, 0, content_start, content_end, data, close is not None))


def parse_document(decoded: DecodedHtml) -> HtmlDocument:
    parser = _Parser(decoded.text)
    parser.feed(decoded.text)
    parser.close()
    merged = []
    for block in parser.raw_blocks:
        if (merged and merged[-1].tag == block.tag
                and merged[-1].content_end == block.content_start):
            previous = merged[-1]
            merged[-1] = RawTextBlock(block.tag, previous.tag_start, previous.tag_end,
                                      previous.content_start, block.content_end,
                                      previous.content + block.content, block.closed)
        else:
            merged.append(block)
    blocks = []
    for block in merged:
        openers = [tag for tag in parser.tags
                   if tag.name == block.tag and tag.end == block.content_start]
        if len(openers) != 1:
            raise ValueError("raw text block without one start tag")
        opener = openers[0]
        blocks.append(RawTextBlock(block.tag, opener.start, opener.end,
                                   block.content_start, block.content_end,
                                   block.content, block.closed))
    return HtmlDocument(decoded, tuple(parser.tags), tuple(blocks))
