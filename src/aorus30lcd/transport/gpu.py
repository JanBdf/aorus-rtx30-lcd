# Vendored from c-drew/gigabyte-aorus-lcd-linux (commit 3aa594f), MIT License,
# Copyright (c) 2026 Alban Creton, Copyright (c) 2026 c-drew. See LICENSE.
"""Find the NVIDIA GPU that carries the LCD, from sysfs and procfs."""
from dataclasses import dataclass
from pathlib import Path
import re

NVIDIA_VENDOR = 0x10DE
GIGABYTE_VENDOR = 0x1458

# Subsystem IDs this project is verified on (RTX 30-series panel, firmware 0x20).
VERIFIED_SUBSYSTEMS = {(0x1458, 0x403E): "AORUS GeForce RTX 3080 MASTER/XTREME rev 2.x"}


@dataclass(frozen=True)
class Gpu:
    pci: str            # e.g. 0000:01:00.0
    minor: int          # /dev/nvidiaN
    subvendor: int
    subdevice: int
    name: str

    @property
    def verified(self) -> bool:
        return (self.subvendor, self.subdevice) in VERIFIED_SUBSYSTEMS

    def describe(self) -> str:
        return f"{self.name} [{self.subvendor:04x}:{self.subdevice:04x}] at {self.pci}"


def _hex(path: Path) -> int:
    return int(path.read_text().strip(), 16)


def list_gpus(sys_root="/sys/bus/pci/devices", proc_root="/proc/driver/nvidia/gpus"):
    gpus = []
    for dev in sorted(Path(sys_root).glob("*")):
        try:
            if _hex(dev / "vendor") != NVIDIA_VENDOR or not (_hex(dev / "class") >> 16) == 0x03:
                continue
            info = (Path(proc_root) / dev.name / "information").read_text()
        except OSError:
            continue
        minor = re.search(r"^Device Minor:\s*(\d+)", info, re.MULTILINE)
        model = re.search(r"^Model:\s*(.+)$", info, re.MULTILINE)
        if not minor:
            continue
        gpus.append(Gpu(dev.name, int(minor.group(1)), _hex(dev / "subsystem_vendor"),
                        _hex(dev / "subsystem_device"), model.group(1).strip() if model else "NVIDIA GPU"))
    return gpus


def find_gpu(pci=None, **roots) -> Gpu:
    """The Gigabyte NVIDIA GPU to use: `pci` if given, else the only Gigabyte card."""
    gpus = list_gpus(**roots)
    if pci:
        match = [g for g in gpus if g.pci.endswith(pci)]
        if len(match) != 1:
            raise LookupError(f"no single NVIDIA GPU matches {pci!r}")
        return match[0]
    gigabyte = [g for g in gpus if g.subvendor == GIGABYTE_VENDOR]
    if len(gigabyte) == 1:
        return gigabyte[0]
    if not gpus:
        raise LookupError("no NVIDIA GPU found (is the nvidia driver loaded?)")
    raise LookupError("expected exactly one Gigabyte NVIDIA GPU; pass --gpu PCI_ADDRESS. Found: "
                      + ", ".join(g.describe() for g in gpus))
