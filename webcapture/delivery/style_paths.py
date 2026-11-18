"""Export path plan and reference rewriting for style captures.

Every supported reference is rewritten in place at the character span of its
*value token*, so the surrounding ``url(``/``)`` or attribute quoting stays as
it was.  Replacements are chosen so that the result is unambiguous in the
context they land in:

* HTML attribute values (``img src``, ``link href``) become one HTML-escaped
  double-quoted value.
* ``url(...)`` values keep their number of quotes: unquoted values are written
  as unquoted CSS with explicit backslash escapes; quoted values get one
  quoted CSS string (with the original quote character still around it).  A
  quoted value inside a ``style`` attribute is additionally HTML-escaped.
* ``@import`` values must stay a string, so they are always written as a
  quoted CSS string (HTML-escaped inside a ``style`` attribute).
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
import html
import posixpath
from webcapture.archive.schema import IntegrityError
from webcapture.archive.style_manifests import BINARY_ROLE, CSS_ROLE
from webcapture.delivery.paths import ensure_relative_output
from webcapture.html.stylesheets import (CONTEXT_ATTRIBUTE, CONTEXT_CSS_TEXT,
                                         CONTEXT_STYLE_ATTRIBUTE, CONTEXT_STYLE_TEXT)

ENTRY_PATH = "index.html"
HTML_DOCUMENT = "html"

_SAFE_UNQUOTED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~/#%")


@dataclass(frozen=True)
class StyleExportPlan:
    entry: str
    documents: Mapping[str, str]
    binaries: Mapping[str, str]

    def document_path(self, document: str) -> str:
        if document == HTML_DOCUMENT:
            return self.entry
        try:
            return self.documents[document]
        except KeyError as exc:
            raise IntegrityError("missing local CSS document path") from exc

    def binary_path(self, request_url: str) -> str:
        try:
            return self.binaries[request_url]
        except KeyError as exc:
            raise IntegrityError("missing local binary path") from exc


def plan_style_export_paths(verified) -> StyleExportPlan:
    """Assign flat, collision-free paths inside the delivery root.

    A flat layout keeps every rewritten reference a plain sibling name, so no
    ``..`` segments are ever needed and no new ambiguity is introduced.
    """
    manifest = verified.style_manifest
    documents = {}
    binaries = {}
    for resource in manifest.resources:
        if CSS_ROLE in resource.roles:
            documents["css:" + resource.request_url] = ensure_relative_output(
                f"style-{len(documents):04d}.css")
        if BINARY_ROLE in resource.roles:
            binaries[resource.request_url] = ensure_relative_output(
                f"media-{len(binaries):04d}.bin")
    entry = ensure_relative_output(ENTRY_PATH)
    if HTML_DOCUMENT not in manifest.documents:
        raise IntegrityError("style capture has no HTML document")
    used = [entry] + list(documents.values()) + list(binaries.values())
    if len(set(used)) != len(used):
        raise IntegrityError("export path collision")
    return StyleExportPlan(entry, MappingProxyType(documents), MappingProxyType(binaries))


def local_style_reference(plan: StyleExportPlan, document: str, reference) -> tuple[str, str]:
    """Return ``(relative_local_path, fragment)`` for one reference."""
    if reference.kind in ("image", "url"):
        target = plan.binary_path(reference.request_url)
    else:
        target = plan.document_path("css:" + reference.request_url)
    source_dir = posixpath.dirname(plan.document_path(document))
    relative = posixpath.relpath(target, source_dir or ".")
    ensure_relative_output(relative)
    return relative, reference.fragment


def css_string(value: str, quote: str = '"') -> str:
    """Quote a local path as a CSS string, keeping the caller's quote character.

    Escapes are always six hexadecimal digits so they can never be confused
    with following data and never need a whitespace terminator.  That keeps
    the result valid in a quoted HTML attribute as well as in raw style text.
    """
    if quote not in ("'", '"'):
        raise IntegrityError("invalid CSS quote character")
    pieces = [quote]
    for character in value:
        if character == "\\":
            pieces.append("\\\\")
        elif character == quote:
            pieces.append("\\" + character)
        elif ord(character) < 32 or ord(character) == 127 or character in "&<>`":
            pieces.append("\\%06x" % ord(character))
        else:
            pieces.append(character)
    pieces.append(quote)
    return "".join(pieces)


def css_unquoted(value: str) -> str:
    """Write a local path as an unquoted CSS URL value.

    Only path-safe characters stay literal; everything else becomes a
    six-digit hexadecimal escape, which is valid in CSS and also safe inside
    an HTML attribute value (no whitespace, quotes or markup characters).
    """
    pieces = []
    for character in value:
        if character in _SAFE_UNQUOTED:
            pieces.append(character)
        else:
            pieces.append("\\%06x" % ord(character))
    return "".join(pieces)


def _replacement(plan, document, reference) -> str:
    relative, fragment = local_style_reference(plan, document, reference)
    value = relative + ("#" + fragment if fragment else "")
    if reference.kind in ("image", "link"):
        if reference.context != CONTEXT_ATTRIBUTE:
            raise IntegrityError("HTML attribute reference in a non-attribute context")
        return '"' + html.escape(value, quote=True) + '"'
    quote = reference.token[0] if reference.token[:1] in ("'", '"') else None
    if reference.kind == "import":
        # ``@import`` only accepts a string, so keep it a quoted string.
        quoted = css_string(value, quote or '"')
    else:
        quoted = css_string(value, quote) if quote else css_unquoted(value)
    if reference.context == CONTEXT_STYLE_ATTRIBUTE:
        # Only the HTML layer is added; the CSS quoting chosen above is
        # already valid inside an attribute value.
        return html.escape(quoted, quote=True)
    return quoted


def rewrite_style_document(plan, document, text, references):
    """Rewrite every supported reference in one decoded document text."""
    pieces = []
    position = len(text)
    for reference in reversed(tuple(references)):
        if reference.document != document:
            raise IntegrityError("reference belongs to another document")
        if reference.end > position or reference.start < 0:
            raise IntegrityError("overlapping export reference spans")
        if reference.context not in (CONTEXT_ATTRIBUTE, CONTEXT_STYLE_ATTRIBUTE,
                                     CONTEXT_STYLE_TEXT, CONTEXT_CSS_TEXT):
            raise IntegrityError("unsupported reference context for export")
        pieces.append(text[reference.end:position])
        pieces.append(_replacement(plan, document, reference))
        position = reference.start
    pieces.append(text[:position])
    pieces.reverse()
    return "".join(pieces)
