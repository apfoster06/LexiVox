import hashlib
import http.server
import re
import threading
from functools import partial

import pytest

from app.models.manager import (
    CatalogEntry, ModelError, ModelFile, ModelManager, Variant, load_catalog,
)

GOOD = b"model weights " * 1000
CFG = b'{"ok": true}'


def _catalog(sha_override=None) -> dict:
    files = (
        ModelFile("config.json", len(CFG), hashlib.sha256(CFG).hexdigest()),
        ModelFile("sub/model.bin", len(GOOD), sha_override or hashlib.sha256(GOOD).hexdigest()),
    )
    v = Variant("tiny", "faster-whisper", "org/repo", "abc123", files)
    return {"tiny": CatalogEntry("tiny", "Tiny", {"faster-whisper": v})}


@pytest.fixture
def hub(tmp_path):
    """A local stand-in for huggingface.co serving /org/repo/resolve/abc123/<file>."""
    root = tmp_path / "hub"
    base = root / "org" / "repo" / "resolve" / "abc123"
    (base / "sub").mkdir(parents=True)
    (base / "config.json").write_bytes(CFG)
    (base / "sub" / "model.bin").write_bytes(GOOD)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_shipped_catalog_is_fully_pinned():
    catalog = load_catalog()
    assert {"tiny", "base", "small", "medium", "large-v3-turbo"} <= set(catalog)
    for entry in catalog.values():
        assert "faster-whisper" in entry.variants and "openvino" in entry.variants
        for v in entry.variants.values():
            assert re.fullmatch(r"[0-9a-f]{40}", v.revision), v.repo
            assert v.files
            for f in v.files:
                assert re.fullmatch(r"[0-9a-f]{64}", f.sha256), f.path
                assert f.size > 0


def test_download_verifies_and_installs(tmp_path, hub):
    m = ModelManager(tmp_path / "models", _catalog())
    seen = []
    spec = m.download("faster-whisper", "tiny", progress=lambda d, t: seen.append((d, t)), base_url=hub)
    assert spec.path == tmp_path / "models" / "faster-whisper" / "tiny"
    assert (spec.path / "sub" / "model.bin").read_bytes() == GOOD
    assert m.is_installed("faster-whisper", "tiny")
    assert seen[-1][0] == seen[-1][1]


def test_hash_mismatch_deletes_file(tmp_path, hub):
    m = ModelManager(tmp_path / "models", _catalog(sha_override="0" * 64))
    with pytest.raises(ModelError, match="SHA-256"):
        m.download("faster-whisper", "tiny", base_url=hub)
    root = tmp_path / "models" / "faster-whisper" / "tiny"
    assert not (root / "sub" / "model.bin").exists()
    assert not list(root.rglob("*.part"))
    assert not m.is_installed("faster-whisper", "tiny")


def test_offline_install_via_rescan(tmp_path):
    m = ModelManager(tmp_path / "models", _catalog())
    root = tmp_path / "models" / "faster-whisper" / "tiny"
    (root / "sub").mkdir(parents=True)
    (root / "config.json").write_bytes(CFG)
    (root / "sub" / "model.bin").write_bytes(GOOD)
    assert not m.is_installed("faster-whisper", "tiny")
    assert m.rescan() == {"tiny": {"faster-whisper": True}}
    assert m.is_installed("faster-whisper", "tiny")

    # Tampering after install is caught by the next rescan.
    (root / "sub" / "model.bin").write_bytes(GOOD[:-1] + b"X")
    assert m.rescan() == {"tiny": {"faster-whisper": False}}
    assert not m.is_installed("faster-whisper", "tiny")


def test_missing_file_means_not_installed(tmp_path, hub):
    m = ModelManager(tmp_path / "models", _catalog())
    m.download("faster-whisper", "tiny", base_url=hub)
    (tmp_path / "models" / "faster-whisper" / "tiny" / "config.json").unlink()
    assert not m.is_installed("faster-whisper", "tiny")


def test_unknown_names_never_become_paths(tmp_path):
    m = ModelManager(tmp_path / "models", _catalog())
    for bad in ("../../etc", "tiny/../..", "small"):
        with pytest.raises(ModelError):
            m.model_dir("faster-whisper", bad)
    with pytest.raises(ModelError):
        m.model_dir("../x", "tiny")


def test_catalog_rejects_unsafe_file_paths(tmp_path):
    bad = tmp_path / "c.toml"
    bad.write_text(
        '[models.x]\nlabel="x"\n[models.x.backends.b]\nrepo="r"\nrevision="v"\n'
        'files=[{path="../evil.dll", size=1, sha256="' + "0" * 64 + '"}]\n'
    )
    with pytest.raises(ModelError):
        load_catalog(bad)
