"""Model catalog, downloads with SHA-256 verification, and offline rescan.

Models live in <data>/models/<backend>/<model>/. A folder counts as installed only
after every file has matched its pinned SHA-256; the result is cached in a
".verified.json" marker. Offline install = copy a folder in, then Rescan.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..backends.base import Cancelled, ModelSpec

log = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).with_name("catalog.toml")
HF_BASE = "https://huggingface.co"
MARKER = ".verified.json"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Variant:
    model: str
    backend: str
    repo: str
    revision: str
    files: tuple[ModelFile, ...]

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    label: str
    variants: dict[str, Variant]


class ModelError(Exception):
    pass


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, CatalogEntry]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    catalog: dict[str, CatalogEntry] = {}
    for name, entry in data.get("models", {}).items():
        variants = {}
        for backend, v in entry.get("backends", {}).items():
            files = tuple(ModelFile(f["path"], int(f["size"]), f["sha256"].lower()) for f in v["files"])
            for f in files:
                _check_relative(f.path)
            variants[backend] = Variant(name, backend, v["repo"], v["revision"], files)
        catalog[name] = CatalogEntry(name, entry.get("label", name), variants)
    return catalog


def _check_relative(rel: str) -> None:
    p = Path(rel)
    if p.is_absolute() or p.drive or ".." in p.parts or rel.startswith(("/", "\\")):
        raise ModelError(f"unsafe path in catalog: {rel!r}")


def sha256_file(path: Path, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


ProgressFn = Callable[[int, int], None]  # (bytes_done, bytes_total)


class ModelManager:
    def __init__(self, models_dir: Path, catalog: dict[str, CatalogEntry] | None = None):
        self.models_dir = models_dir
        self.catalog = catalog if catalog is not None else load_catalog()
        self._lock = threading.Lock()

    def variant(self, backend: str, model: str) -> Variant:
        entry = self.catalog.get(model)
        if entry is None or backend not in entry.variants:
            raise ModelError(f"unknown model {model!r} for {backend!r}")
        return entry.variants[backend]

    def model_dir(self, backend: str, model: str) -> Path:
        self.variant(backend, model)  # only catalog names ever become paths
        return self.models_dir / backend / model

    def _file_path(self, root: Path, f: ModelFile) -> Path:
        target = (root / f.path).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ModelError(f"unsafe path {f.path!r}")
        return target

    def is_installed(self, backend: str, model: str) -> bool:
        v = self.variant(backend, model)
        root = self.model_dir(backend, model)
        try:
            marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if marker.get("revision") != v.revision:
            return False
        # Cheap re-check on every call: files still exist with the verified sizes.
        for f in v.files:
            p = root / f.path
            if not p.is_file() or p.stat().st_size != f.size:
                return False
        return True

    def spec(self, backend: str, model: str) -> ModelSpec | None:
        if not self.is_installed(backend, model):
            return None
        return ModelSpec(model, self.model_dir(backend, model))

    def verify(self, backend: str, model: str, cancel: threading.Event | None = None) -> bool:
        """Full SHA-256 check of a folder; writes or removes the marker."""
        v = self.variant(backend, model)
        root = self.model_dir(backend, model)
        marker = root / MARKER
        ok = root.is_dir()
        if ok:
            for f in v.files:
                p = self._file_path(root, f)
                if not p.is_file() or p.stat().st_size != f.size or sha256_file(p, cancel) != f.sha256:
                    log.warning("model %s/%s: %s failed verification", backend, model, f.path)
                    ok = False
                    break
        if ok:
            marker.write_text(json.dumps({"revision": v.revision, "repo": v.repo}), encoding="utf-8")
        elif marker.exists():
            marker.unlink()
        return ok

    def rescan(self, cancel: threading.Event | None = None) -> dict[str, dict[str, bool]]:
        """Verify every catalog folder present on disk (for offline installs)."""
        result: dict[str, dict[str, bool]] = {}
        for name, entry in self.catalog.items():
            for backend in entry.variants:
                if self.model_dir(backend, name).is_dir():
                    result.setdefault(name, {})[backend] = self.verify(backend, name, cancel)
        return result

    def download(
        self,
        backend: str,
        model: str,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
        base_url: str = HF_BASE,
    ) -> ModelSpec:
        if not self._lock.acquire(blocking=False):
            raise ModelError("another download is already running")
        try:
            return self._download(backend, model, progress, cancel, base_url)
        finally:
            self._lock.release()

    def _download(self, backend, model, progress, cancel, base_url) -> ModelSpec:
        v = self.variant(backend, model)
        root = self.model_dir(backend, model)
        root.mkdir(parents=True, exist_ok=True)
        (root / MARKER).unlink(missing_ok=True)
        total = v.total_size
        done = 0
        for f in v.files:
            target = self._file_path(root, f)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.stat().st_size == f.size and sha256_file(target, cancel) == f.sha256:
                done += f.size  # already present and valid (resumed download)
                if progress:
                    progress(done, total)
                continue
            url = f"{base_url}/{v.repo}/resolve/{v.revision}/{urllib.parse.quote(f.path)}"
            done = self._fetch(url, target, f, done, total, progress, cancel)
        if not self.verify(backend, model, cancel):
            raise ModelError("verification failed after download")
        log.info("model %s/%s installed", backend, model)
        return ModelSpec(model, root)

    def _fetch(self, url, target: Path, f: ModelFile, done, total, progress, cancel) -> int:
        part = target.with_name(target.name + ".part")
        h = hashlib.sha256()
        req = urllib.request.Request(url, headers={"User-Agent": "lexivox"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:
                while chunk := resp.read(_CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            size = part.stat().st_size
            if size != f.size or h.hexdigest() != f.sha256:
                raise ModelError(f"{f.path}: SHA-256 mismatch, file deleted")
            os.replace(part, target)
            return done
        except Exception:
            part.unlink(missing_ok=True)
            raise
