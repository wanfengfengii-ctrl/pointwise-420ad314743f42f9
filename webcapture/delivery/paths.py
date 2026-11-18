from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping
import posixpath
from webcapture.archive.schema import IntegrityError


def ensure_relative_output(path: str) -> str:
    if not isinstance(path, str) or not path or any(
            c in path for c in ("\\", ":", "?", "#", "\x00")):
        raise ValueError("unsafe output path")
    parts = path.split("/")
    if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError("output path must stay inside its root")
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError("output path contains controls")
    if PurePosixPath(path).is_absolute():
        raise ValueError("absolute output path")
    return path


@dataclass(frozen=True)
class ExportPathPlan:
    entry: str
    images: Mapping[str, str]


def plan_export_paths(verified) -> ExportPathPlan:
    entry = ensure_relative_output("index.html")
    paths = {request: ensure_relative_output(f"image-{ordinal:04d}.bin")
             for ordinal, (request, _) in enumerate(verified.view.manifest.images)}
    if len(set(paths.values()) | {entry}) != len(paths) + 1:
        raise IntegrityError("export path collision")
    return ExportPathPlan(entry, MappingProxyType(paths))


def local_image_reference(plan: ExportPathPlan, request_url: str, fragment: str) -> str:
    if request_url not in plan.images:
        raise IntegrityError("missing local image path")
    target = ensure_relative_output(plan.images[request_url])
    entry = ensure_relative_output(plan.entry)
    relative = posixpath.relpath(target, posixpath.dirname(entry) or ".")
    ensure_relative_output(relative)
    return relative + ("#" + fragment if fragment else "")
