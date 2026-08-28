"""Detects what this machine can do, and sizes the local models to match.

The same build is expected to run on a 15W laptop, a many-core server and a
box with a dedicated GPU, and the right settings differ sharply between them.
Hard-coding one set means either wasting a large machine or making a small
one unusable, so the defaults are derived at startup instead and every one of
them stays overridable from the environment.

Three things are decided here:

- **Where the models run.** A CUDA, ROCm or CoreML provider is used when
  onnxruntime exposes one, and the CPU otherwise.
- **How many cores they may take.** onnxruntime claims every core by default.
  On a server that is what you want; on a laptop it means ingesting a filing
  makes the whole machine unresponsive for minutes. A few cores are held back,
  scaled so a small machine keeps a usable share and a large one is not left
  idle.
- **How much work goes through at once.** Bigger batches pay off on a GPU or
  with plenty of memory, and cost memory on a machine that has little.

Detection is deliberately defensive: an unfamiliar platform falls back to
conservative values rather than raising, because failing to start is far worse
than running at the speed of a smaller machine.
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)

# Providers that mean "not the CPU", in preference order. Which of these is
# present depends entirely on which onnxruntime build is installed, so this is
# a list of what to *use if offered* rather than a claim about any machine.
#
#   TensorRT / CUDA   NVIDIA. `onnxruntime-gpu`.
#   MIGraphX / ROCm   AMD on Linux. AMD's own wheel index, not PyPI.
#   DirectML          AMD, Intel and NVIDIA on Windows. `onnxruntime-directml`.
#                     The only GPU path for an AMD card on Windows.
#   CoreML            Apple silicon. Ships in the default macOS wheel -
#                     verified present in the compiled library - so it needs
#                     nothing installed and is picked up automatically.
#
# TensorRT before CUDA and MIGraphX before ROCm because each is the vendor's
# optimising layer over the other, and both only appear when deliberately
# installed.
_ACCELERATED_PROVIDERS = (
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "MIGraphXExecutionProvider",
    "ROCMExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
)

# Shown to the operator, because "DmlExecutionProvider" does not tell most
# people whether their AMD card is being used.
PROVIDER_LABELS = {
    "TensorrtExecutionProvider": "NVIDIA GPU (TensorRT)",
    "CUDAExecutionProvider": "NVIDIA GPU (CUDA)",
    "MIGraphXExecutionProvider": "AMD GPU (MIGraphX)",
    "ROCMExecutionProvider": "AMD GPU (ROCm)",
    "DmlExecutionProvider": "GPU via DirectML",
    "CoreMLExecutionProvider": "Apple silicon (CoreML)",
}


def _total_ram_gb() -> float:
    """Physical memory, or 0.0 when it cannot be determined."""
    try:
        if platform.system() == "Windows":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / 1024**3

        # POSIX: available on Linux and macOS alike.
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 1024**3
    except Exception:  # unfamiliar platform, restricted container, anything
        return 0.0


def _accelerator() -> str | None:
    """The best non-CPU provider onnxruntime offers here, if any.

    A GPU is only visible when the GPU build of onnxruntime is installed;
    the default wheel is CPU-only, which is why this reports honestly rather
    than assuming.
    """
    try:
        import onnxruntime

        available = set(onnxruntime.get_available_providers())
    except Exception:
        return None
    return next((p for p in _ACCELERATED_PROVIDERS if p in available), None)


def _reserved_cores(cpu_count: int) -> int:
    """Cores held back so the machine stays usable while models run.

    Proportional at the small end and capped at the large one: reserving four
    of four cores would leave nothing to work with, and reserving a quarter of
    sixty-four would leave a server idling.
    """
    return min(4, max(1, cpu_count // 4))


@dataclass(frozen=True)
class HardwareProfile:
    cpu_count: int
    ram_gb: float
    accelerator: str | None

    @property
    def on_gpu(self) -> bool:
        return self.accelerator is not None

    @property
    def onnx_threads(self) -> int | None:
        """Cores the ONNX sessions may use. None means "the library default".

        On an accelerator the CPU is only marshalling work, so it is left
        alone.
        """
        if self.on_gpu:
            return None
        return max(1, self.cpu_count - _reserved_cores(self.cpu_count))

    @property
    def providers(self) -> list[str] | None:
        """Explicit provider order, or None to let fastembed decide."""
        if not self.accelerator:
            return None
        return [self.accelerator, "CPUExecutionProvider"]

    @property
    def embed_batch_size(self) -> int:
        """Passages per forward pass.

        A GPU wants a large batch to be busy at all. On the CPU the batch
        mostly costs memory, so it tracks what the machine has rather than
        what it could theoretically hold.
        """
        if self.on_gpu:
            return 256
        # Measured on a 12-core laptop: 8 -> 815ms/passage, 32 -> 733,
        # 64 -> 707, 256 -> 721. The curve is flat past 64, so there is
        # nothing to gain by holding more in memory than that.
        if self.ram_gb and self.ram_gb <= 4:
            return 16
        if self.ram_gb and self.ram_gb < 8:
            return 32
        return 64

    @property
    def rerank_batch_size(self) -> int:
        # Reranking sees a shortlist, never a whole filing, so this is small
        # by nature; it only needs to not be pathological.
        return 64 if self.on_gpu else 16

    @property
    def accelerator_label(self) -> str:
        return PROVIDER_LABELS.get(self.accelerator or "", self.accelerator or "CPU")

    def describe(self) -> str:
        where = self.accelerator_label
        threads = self.onnx_threads or "all"
        return (
            f"{where}, {self.cpu_count} cores (using {threads}), "
            f"{self.ram_gb:.1f}GB RAM, embed batch {self.embed_batch_size}"
        )


@lru_cache(maxsize=1)
def detect() -> HardwareProfile:
    """The machine's profile. Cached - none of this changes while we run."""
    profile = HardwareProfile(
        cpu_count=os.cpu_count() or 1,
        ram_gb=_total_ram_gb(),
        accelerator=_accelerator(),
    )
    logger.info("Hardware: %s", profile.describe())
    return profile
