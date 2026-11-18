from dataclasses import dataclass
from pathlib import Path
from html import escape
import shutil
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.paths import plan_export_paths, local_image_reference, ensure_relative_output


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
        replacement = '"' + escape(local_image_reference(
            plan, reference.request_url, reference.fragment), quote=True) + '"'
        text = text[:reference.start] + replacement + text[reference.end:]
    return text.encode("utf-8")


def export_capture(archive, capture_id: str, target, *, writer=write_file_exclusive) -> str:
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError(str(target))
    verified = verify_capture(archive, capture_id)
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
