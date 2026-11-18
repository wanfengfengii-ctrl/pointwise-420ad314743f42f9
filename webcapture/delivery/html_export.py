"""Offline export of a verified capture.

``export_capture`` accepts both V1 captures and style captures and returns the
relative HTML entry path inside the delivery directory.
"""

from dataclasses import dataclass
from pathlib import Path
from html import escape as html_escape
import shutil
from webcapture.archive.manifests import ManifestStore, is_style_payload
from webcapture.archive.publication import PublicationStore
from webcapture.delivery.paths import plan_export_paths, local_image_reference, ensure_relative_output
from webcapture.delivery.verification import verify_capture

IMAGE_PATH = "image-{ordinal:04d}.bin"
ENTRY_PATH = "index.html"


def write_file_exclusive(path: Path, body: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(body)


@dataclass
class ExportCandidate:
    target: Path
    owned: bool = False
    state: str = "mapped"

    def acquire(self) -> None:
        if self.state != "mapped":
            raise ValueError("export candidate cannot be acquired twice")
        self.target.mkdir()
        self.owned = True
        self.state = "writing"

    def write(self, relative: str, body: bytes, writer=write_file_exclusive) -> None:
        if not self.owned or self.state != "writing":
            raise ValueError("export candidate is not writable")
        path = self.target.joinpath(*ensure_relative_output(relative).split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        writer(path, body)

    def deliver(self) -> None:
        if self.state != "writing" or not self.owned:
            raise ValueError("export candidate is not writing")
        self.state = "delivered"

    def abort(self) -> None:
        if self.state == "delivered":
            raise ValueError("delivered export cannot be aborted")
        if self.owned:
            shutil.rmtree(self.target)
            self.owned = False
        self.state = "aborted"


def rewrite_direct_images(verified, plan) -> bytes:
    text = verified.html.text
    for reference in reversed(verified.view.manifest.references):
        replacement = '"' + html_escape(local_image_reference(
            plan, reference.request_url, reference.fragment), quote=True) + '"'
        text = text[:reference.start] + replacement + text[reference.end:]
    return text.encode("utf-8")


def _export_page(verified, target, writer) -> str:
    plan = plan_export_paths(verified)
    derived = rewrite_direct_images(verified, plan)
    candidate = ExportCandidate(target)
    try:
        candidate.acquire()
        for request, identity in verified.view.manifest.images:
            candidate.write(plan.images[request], verified.view.evidence[identity].response.body, writer)
        candidate.write(plan.entry, derived, writer)
        candidate.deliver()
        return plan.entry
    except BaseException:
        candidate.abort()
        raise


def export_capture(archive, capture_id: str, target, *, writer=write_file_exclusive) -> str:
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError(str(target))
    PublicationStore(archive).require_published(capture_id)
    with archive.transaction():
        raw = ManifestStore(archive).verified_raw(capture_id)
        style = is_style_payload(raw)
    if style:
        from webcapture.delivery.style_export import export_style_capture
        return export_style_capture(archive, capture_id, target, writer=writer)
    return _export_page(verify_capture(archive, capture_id), target, writer)
