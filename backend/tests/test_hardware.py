"""The same build has to run well on very different machines.

Evaluation happens on a mix of hardware - thin laptops, many-core servers,
boxes with a dedicated GPU - so the model settings are derived at startup
rather than fixed. These tests pin the shape of that derivation, because
getting it wrong is quiet: the app still works, it is just needlessly slow on
a big machine or hostile to use on a small one.
"""

from __future__ import annotations

import pytest

from app.hardware import HardwareProfile, detect


def cpu(cores: int, ram: float = 16.0) -> HardwareProfile:
    return HardwareProfile(cpu_count=cores, ram_gb=ram, accelerator=None)


def gpu(cores: int = 16, ram: float = 64.0) -> HardwareProfile:
    return HardwareProfile(cpu_count=cores, ram_gb=ram, accelerator="CUDAExecutionProvider")


# --- cores ----------------------------------------------------------------


@pytest.mark.parametrize("cores", [1, 2, 4, 8, 12, 32, 64, 128])
def test_at_least_one_core_is_always_usable(cores):
    """Reserving headroom must never reserve the whole machine."""
    assert cpu(cores).onnx_threads >= 1


@pytest.mark.parametrize("cores", [4, 8, 12, 32, 128])
def test_some_headroom_is_always_left_on_a_cpu_machine(cores):
    """The reason this exists: a saturated laptop stops responding at all."""
    assert cpu(cores).onnx_threads < cores


def test_a_small_machine_keeps_a_usable_share_of_itself():
    # Reserving a flat four cores would leave a four-core laptop with none.
    assert cpu(4).onnx_threads == 3
    assert cpu(2).onnx_threads == 1


def test_a_large_machine_is_not_left_idling():
    """Headroom is a courtesy, not a quarter of a server."""
    assert cpu(64).onnx_threads == 60
    assert cpu(128).onnx_threads == 124


def test_more_cores_never_means_fewer_threads():
    threads = [cpu(c).onnx_threads for c in (2, 4, 8, 16, 32, 64, 128)]
    assert threads == sorted(threads)


# --- accelerators ---------------------------------------------------------


def test_a_gpu_is_used_when_one_is_available():
    assert gpu().providers[0] == "CUDAExecutionProvider"
    # The CPU stays in the list as the fallback for any unsupported op.
    assert gpu().providers[-1] == "CPUExecutionProvider"


def test_the_cpu_is_not_throttled_when_the_gpu_does_the_work():
    """Holding cores back protects a machine doing the inference itself."""
    assert gpu().onnx_threads is None


def test_no_provider_is_forced_when_there_is_no_accelerator():
    assert cpu(8).providers is None


@pytest.mark.parametrize(
    "provider", ["CUDAExecutionProvider", "ROCMExecutionProvider", "CoreMLExecutionProvider"]
)
def test_every_supported_accelerator_is_recognised(provider):
    profile = HardwareProfile(cpu_count=8, ram_gb=32.0, accelerator=provider)
    assert profile.on_gpu
    assert profile.providers[0] == provider


# --- batching -------------------------------------------------------------


def test_a_gpu_gets_a_batch_large_enough_to_keep_it_busy():
    assert gpu().embed_batch_size >= 256


def test_batch_size_shrinks_on_a_machine_short_of_memory():
    assert cpu(4, ram=4.0).embed_batch_size < cpu(4, ram=32.0).embed_batch_size


def test_batch_size_never_collapses_to_something_pathological():
    assert cpu(1, ram=2.0).embed_batch_size >= 8


def test_more_memory_never_means_a_smaller_batch():
    sizes = [cpu(8, ram=r).embed_batch_size for r in (2, 4, 8, 16, 64, 512)]
    assert sizes == sorted(sizes)


# --- detection ------------------------------------------------------------


def test_detection_works_on_this_machine_whatever_it_is():
    """Must never raise: failing to start is worse than running slowly."""
    profile = detect()
    assert profile.cpu_count >= 1
    assert profile.ram_gb >= 0
    assert profile.onnx_threads is None or profile.onnx_threads >= 1
    assert profile.embed_batch_size >= 8
    assert profile.describe()


def test_unknown_memory_does_not_break_the_derivation():
    """_total_ram_gb returns 0.0 when it cannot tell; that must be survivable."""
    profile = HardwareProfile(cpu_count=8, ram_gb=0.0, accelerator=None)
    assert profile.embed_batch_size >= 8
    assert profile.onnx_threads >= 1


# --- vendor coverage ------------------------------------------------------
#
# Evaluation runs on a mix of machines, and the failure mode for a missing
# provider is silent: the app works, just on the CPU, with no indication that
# the GPU it was meant to use was never considered.


@pytest.mark.parametrize(
    "provider, expected_vendor",
    [
        ("CUDAExecutionProvider", "NVIDIA"),
        ("TensorrtExecutionProvider", "NVIDIA"),
        ("ROCMExecutionProvider", "AMD"),
        ("MIGraphXExecutionProvider", "AMD"),
        ("DmlExecutionProvider", "DirectML"),
        ("CoreMLExecutionProvider", "Apple"),
    ],
)
def test_every_vendor_path_is_recognised_and_named(provider, expected_vendor):
    profile = HardwareProfile(cpu_count=8, ram_gb=32.0, accelerator=provider)
    assert profile.on_gpu, f"{provider} was not treated as an accelerator"
    assert profile.providers[0] == provider
    assert expected_vendor.lower() in profile.accelerator_label.lower()


def test_directml_is_recognised_because_it_is_the_only_amd_path_on_windows():
    """AMD on Windows cannot use CUDA or ROCm - DirectML or nothing."""
    profile = HardwareProfile(cpu_count=8, ram_gb=32.0, accelerator="DmlExecutionProvider")
    assert profile.on_gpu
    assert profile.embed_batch_size == 256


def test_a_faster_provider_is_preferred_when_several_are_offered(monkeypatch):
    """A CUDA box on Windows may expose DirectML too; CUDA should win."""
    import app.hardware as hardware

    class FakeOrt:
        @staticmethod
        def get_available_providers():
            return ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]

    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", FakeOrt)
    assert hardware._accelerator() == "CUDAExecutionProvider"


def test_an_unknown_provider_is_not_mistaken_for_an_accelerator(monkeypatch):
    import app.hardware as hardware

    class FakeOrt:
        @staticmethod
        def get_available_providers():
            return ["CPUExecutionProvider", "AzureExecutionProvider"]

    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", FakeOrt)
    assert hardware._accelerator() is None


def test_a_cpu_only_machine_is_labelled_plainly():
    assert HardwareProfile(8, 16.0, None).accelerator_label == "CPU"
