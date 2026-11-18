"""The finite CSS lexical subset shared with fixtures/lexical.json.

Only real ``url(...)`` functions and ``@import`` values produce tokens.
Comments and ordinary strings stay opaque.  Offsets are half-open Unicode
character intervals into the supplied text.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CssToken:
    kind: str
    start: int
    end: int
    raw: str
    decoded: str


_WS = " \t\r\n\f"
_HEX = "0123456789abcdefABCDEF"
_NAME_EXTRA = "_-"


def _is_name_char(character: str) -> bool:
    return character.isalnum() or character in _NAME_EXTRA or ord(character) >= 128


def _escape(text: str, index: int) -> tuple[str, int]:
    """Decode the CSS escape starting at ``text[index] == '\\'``."""
    size = len(text)
    if index + 1 >= size:
        raise ValueError("dangling CSS backslash escape")
    following = text[index + 1]
    if following in "\r\n\f":
        raise ValueError("backslash-newline outside a CSS string")
    if following in _HEX:
        end = index + 1
        while end < size and end < index + 7 and text[end] in _HEX:
            end += 1
        code = int(text[index + 1:end], 16)
        if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
            raise ValueError("invalid escaped CSS code point")
        if end < size and text[end] in _WS:
            if text[end] == "\r" and end + 1 < size and text[end + 1] == "\n":
                end += 2
            else:
                end += 1
        return chr(code), end
    return following, index + 2


def _string(text: str, index: int) -> tuple[str, int]:
    """Read the quoted string at ``index``; return decoded value and end."""
    quote = text[index]
    size = len(text)
    position = index + 1
    value = []
    while position < size:
        character = text[position]
        if character == quote:
            return "".join(value), position + 1
        if character == "\\":
            if position + 1 < size and text[position + 1] in "\r\n\f":
                position += 2
                if text[position - 1] == "\r" and position < size and text[position] == "\n":
                    position += 1
                continue
            decoded, position = _escape(text, position)
            value.append(decoded)
            continue
        if character in "\r\n\f":
            raise ValueError("unescaped newline in CSS string")
        if character == "\x00":
            raise ValueError("NUL in CSS string")
        value.append(character)
        position += 1
    raise ValueError("unterminated CSS string")


def _identifier(text: str, index: int) -> tuple[str, int]:
    size = len(text)
    position = index
    value = []
    while position < size:
        character = text[position]
        if character == "\\":
            decoded, position = _escape(text, position)
            value.append(decoded)
        elif _is_name_char(character):
            value.append(character)
            position += 1
        else:
            break
    return "".join(value), position


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in _WS:
        index += 1
    return index


def _skip_ws_comments(text: str, index: int) -> int:
    size = len(text)
    position = index
    while position < size:
        if text[position] in _WS:
            position += 1
        elif text.startswith("/*", position):
            close = text.find("*/", position + 2)
            if close < 0:
                raise ValueError("unterminated CSS comment")
            position = close + 2
        else:
            break
    return position


def _url_value(text: str, index: int) -> tuple[int, int, str, int]:
    """Read one URL value starting after ``(``.

    Returns ``(value_start, value_end, decoded, index_after_paren)``.
    """
    size = len(text)
    position = _skip_ws(text, index)
    if position >= size:
        raise ValueError("unterminated CSS url value")
    if text[position] in "\"'":
        quote = text[position]
        start = position
        decoded, position = _string(text, position)
        end = position
        position = _skip_ws(text, position)
        if position >= size or text[position] != ")":
            raise ValueError("unterminated CSS url function")
        if not decoded:
            raise ValueError("empty CSS url value")
        return start, end, decoded, position + 1
    start = position
    value = []
    while position < size and text[position] != ")":
        character = text[position]
        if character == "\\":
            decoded, position = _escape(text, position)
            value.append(decoded)
            continue
        if character in _WS:
            break
        if character in "\"'":
            raise ValueError("quote inside unquoted CSS url value")
        if character == "(":
            raise ValueError("unescaped parenthesis inside CSS url value")
        if ord(character) < 32 or ord(character) == 127:
            raise ValueError("control character inside CSS url value")
        value.append(character)
        position += 1
    end = position
    decoded = "".join(value)
    if not decoded:
        raise ValueError("empty CSS url value")
    position = _skip_ws(text, position)
    if position >= size or text[position] != ")":
        raise ValueError("unterminated CSS url function")
    return start, end, decoded, position + 1


def _import_value(text: str, index: int) -> tuple[int, int, str, int]:
    size = len(text)
    position = _skip_ws_comments(text, index)
    if position >= size:
        raise ValueError("unterminated CSS import")
    if text[position] in "\"'":
        start = position
        decoded, position = _string(text, position)
        end = position
        if not decoded:
            raise ValueError("empty CSS import value")
        return start, end, decoded, position
    identifier, after = _identifier(text, position)
    if identifier.lower() != "url":
        raise ValueError("unsupported CSS import value")
    position = _skip_ws(text, after)
    if position >= size or text[position] != "(":
        raise ValueError("unsupported CSS import value")
    start, end, decoded, after = _url_value(text, position + 1)
    return start, end, decoded, after


def scan_css(text: str) -> tuple[CssToken, ...]:
    if not isinstance(text, str):
        raise ValueError("CSS text must be a string")
    if "\x00" in text:
        raise ValueError("NUL in CSS text")
    size = len(text)
    tokens = []
    index = 0
    while index < size:
        character = text[index]
        if character == "/" and index + 1 < size and text[index + 1] == "*":
            close = text.find("*/", index + 2)
            if close < 0:
                raise ValueError("unterminated CSS comment")
            index = close + 2
        elif character in "\"'":
            _, index = _string(text, index)
        elif character == "@":
            identifier, after = _identifier(text, index + 1)
            if identifier.lower() != "import":
                raise ValueError("unsupported CSS at-rule")
            start, end, decoded, after = _import_value(text, after)
            after = _skip_ws_comments(text, after)
            if after >= size or text[after] != ";":
                raise ValueError("CSS import requires a terminating semicolon")
            tokens.append(CssToken("import", start, end, text[start:end], decoded))
            index = after + 1
        elif _is_name_char(character) or character == "\\":
            identifier, after = _identifier(text, index)
            if not identifier:
                index += 1
                continue
            if identifier.lower() == "url":
                position = _skip_ws(text, after)
                if position < size and text[position] == "(":
                    start, end, decoded, after = _url_value(text, position + 1)
                    tokens.append(CssToken("url", start, end, text[start:end], decoded))
                    index = after
                    continue
            index = after if after > index else index + 1
        else:
            index += 1
    return tuple(tokens)
