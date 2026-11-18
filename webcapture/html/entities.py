"""HTML character-reference decoding with a raw-text position map."""

import html

_CHARREF = html._charref


def decode_with_map(raw: str) -> tuple[str, tuple[tuple[int, int, bool], ...]]:
    """Decode character references and report each output character's source span.

    ``spans[i]`` is ``(raw_start, raw_end, continuation)`` for decoded
    character ``i``.  ``continuation`` is true when the character came from
    the same character reference as the one before it.  A reference expanding
    to several characters therefore shares one raw span; a caller that wants
    to replace a sub-range of the decoded text must not cut such a span.
    """
    if not isinstance(raw, str):
        raise ValueError("attribute value must be a string")
    characters = []
    spans = []
    position = 0
    size = len(raw)
    while position < size:
        if raw[position] == "&":
            match = _CHARREF.match(raw, position)
            if match is not None:
                decoded = html.unescape(match.group())
                for index, character in enumerate(decoded):
                    characters.append(character)
                    spans.append((position, match.end(), index > 0))
                position = match.end()
                continue
        characters.append(raw[position])
        spans.append((position, position + 1, False))
        position += 1
    return "".join(characters), tuple(spans)
