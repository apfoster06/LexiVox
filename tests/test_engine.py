"""Backend auto-selection and GPU -> CPU fallback, with hardware mocked."""

import threading

import pytest
from conftest import FakeBackend, install_fake_model

from app.backends.base import Device, TranscribeOptions
from app.config import SettingsStore
from app.engine import Choice, Engine, ModelMissing, auto_order, recommended_model
from app.models.manager import ModelManager, load_catalog

CPU = Device("cpu", "", "CPU", "cpu")
NV = Device("gpu", "nvidia", "RTX", "cuda:0")
IGPU = Device("gpu", "intel", "Arc 130V", "GPU")
NPU = Device("npu", "intel", "Intel NPU", "NPU")
AMD = Device("gpu", "amd", "Radeon", "vulkan:0")


def pick(pairs, cpu_winner=None):
    return auto_order([Choice(b, d) for b, d in pairs], cpu_winner)[0]


def test_cuda_dll_setup_is_harmless_without_nvidia_packages():
    import app.backends.faster_whisper_backend as fw

    fw._dll_dirs_added = False
    fw.add_cuda_dll_dirs()  # must not raise on a machine without the "nvidia" extra


def test_nvidia_only():
    c = pick([("faster-whisper", CPU), ("faster-whisper", NV)])
    assert (c.backend, c.device) == ("faster-whisper", NV)


def test_intel_gpu_and_npu_prefers_gpu():
    pairs = [("openvino", CPU), ("openvino", NPU), ("openvino", IGPU), ("faster-whisper", CPU)]
    assert pick(pairs).device == IGPU
    # NPU only when there's no Intel GPU choice
    assert pick([p for p in pairs if p[1] != IGPU]).device == NPU


def test_amd_only_uses_whispercpp():
    c = pick([("faster-whisper", CPU), ("whispercpp", AMD), ("whispercpp", CPU)])
    assert (c.backend, c.device) == ("whispercpp", AMD)


def test_cpu_only_prefers_faster_whisper_unless_benchmark_says_otherwise():
    pairs = [("openvino", CPU), ("faster-whisper", CPU)]
    assert pick(pairs).backend == "faster-whisper"
    assert pick(pairs, cpu_winner="openvino").backend == "openvino"


def test_nvidia_beats_intel():
    assert pick([("openvino", IGPU), ("faster-whisper", NV)]).device == NV


def test_recommended_model():
    assert recommended_model(Choice("openvino", IGPU), 8) == "large-v3-turbo"
    assert recommended_model(Choice("openvino", NPU), 4) == "large-v3-turbo"
    assert recommended_model(Choice("faster-whisper", CPU), 8) == "small"
    assert recommended_model(Choice("faster-whisper", CPU), 4) == "base"


@pytest.fixture
def setup(tmp_path):
    models = ModelManager(tmp_path / "models", load_catalog())
    settings = SettingsStore(tmp_path / "config.json")
    return models, settings


def _run(engine, restarts=None):
    segs = []
    return engine.transcribe(
        "x.wav", "tiny", TranscribeOptions(), segs.append,
        (restarts if restarts is not None else []).append, threading.Event(),
    ), segs


def test_gpu_failure_falls_back_to_cpu_same_backend(setup):
    models, settings = setup
    ov = FakeBackend("openvino", devices=[IGPU, CPU], fail_on="gpu")
    install_fake_model(models, "openvino", "tiny")
    engine = Engine({"openvino": ov}, models, settings)
    restarts = []
    (segments, used, notice), _ = _run(engine, restarts)
    assert used.device == CPU and used.backend == "openvino"
    assert notice and "CPU" in notice
    assert restarts == [notice]
    assert [s.text for s in segments] == ["hello", "world"]
    assert ov.calls == ["load:GPU", "load:cpu"]


def test_cpu_failure_is_not_retried(setup):
    models, settings = setup
    fw = FakeBackend("faster-whisper", devices=[CPU], fail_on="cpu")
    install_fake_model(models, "faster-whisper", "tiny")
    with pytest.raises(RuntimeError):
        _run(Engine({"faster-whisper": fw}, models, settings))


def test_missing_model_raises(setup):
    models, settings = setup
    engine = Engine({"faster-whisper": FakeBackend()}, models, settings)
    with pytest.raises(ModelMissing):
        _run(engine)


def test_force_cpu_and_manual_override(setup):
    models, settings = setup
    ov = FakeBackend("openvino", devices=[IGPU, NPU, CPU])
    engine = Engine({"openvino": ov, "faster-whisper": FakeBackend()}, models, settings)
    assert engine.current().device == IGPU
    settings.update(backend="openvino", device="NPU")
    assert engine.current().device == NPU
    settings.update(force_cpu=True)
    assert engine.current().device.kind == "cpu"


def test_saved_benchmark_winner_is_used(setup):
    models, settings = setup
    engine = Engine({"openvino": FakeBackend("openvino", devices=[IGPU, NPU, CPU])}, models, settings)
    settings.update(benchmark={"best": "openvino|NPU"})
    assert engine.current().device == NPU
    settings.update(benchmark={"best": "openvino|GONE"})  # stale entry is ignored
    assert engine.current().device == IGPU
