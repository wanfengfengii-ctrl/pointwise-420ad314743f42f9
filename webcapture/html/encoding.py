from dataclasses import dataclass
from bisect import bisect_left


@dataclass(frozen=True)
class DecodedHtml:
    original: bytes
    text: str
    byte_boundaries: tuple[int, ...]

    def byte_offset(self, character_offset: int) -> int:
        if type(character_offset) is not int or not 0 <= character_offset <= len(self.text):
            raise ValueError("character offset outside document")
        return self.byte_boundaries[character_offset]

    def character_offset(self, byte_offset: int) -> int:
        if type(byte_offset) is not int:
            raise ValueError("byte offset must be an integer")
        index = bisect_left(self.byte_boundaries, byte_offset)
        if index == len(self.byte_boundaries) or self.byte_boundaries[index] != byte_offset:
            raise ValueError("byte offset is not a UTF-8 character boundary")
        return index


def decode_html(body: bytes) -> DecodedHtml:
    if not isinstance(body, bytes):
        raise ValueError("HTML body must be bytes")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("HTML is not strict UTF-8") from exc
    offsets = [0]
    for character in text:
        offsets.append(offsets[-1] + len(character.encode("utf-8")))
    return DecodedHtml(body, text, tuple(offsets))
