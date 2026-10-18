"""Value sources for `live`: nvidia-smi, hwmon, /proc/stat, constants or a shell command."""
import subprocess
import sys
from pathlib import Path

GPU_SOURCES = ("gpu_temp", "gpu_clock", "gpu_usage", "gpu_fan", "vram_clock", "vram_usage", "gpu_power")
SOURCES = GPU_SOURCES + ("cpu_temp", "cpu_usage", "zero")
DEFAULT_SOURCES = {"temp": "gpu_temp", "clock": "gpu_clock", "usage": "gpu_usage", "fan": "gpu_fan",
                   "vram_clock": "vram_clock", "vram": "vram_usage", "fps": "zero", "power": "gpu_power"}


class Sources:
    """nvidia-smi is queried once per tick, and only if a slot needs it."""
    NV = "temperature.gpu,clocks.gr,utilization.gpu,fan.speed,clocks.mem,memory.used,memory.total,power.draw"

    def __init__(self):
        self._cpu_prev = None

    def gpu(self):
        out = subprocess.run(["nvidia-smi", f"--query-gpu={self.NV}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.split("\n")[0]
        f = [float(x) if x.strip().replace(".", "", 1).isdigit() else 0.0 for x in out.split(",")]
        t, clk, use, fan, mclk, used, total, pwr = (f + [0.0] * 8)[:8]
        return {"gpu_temp": t, "gpu_clock": clk, "gpu_usage": use, "gpu_fan": fan, "vram_clock": mclk,
                "vram_usage": 100 * used / total if total else 0, "gpu_power": pwr}

    @staticmethod
    def cpu_temp():
        for h in Path("/sys/class/hwmon").glob("hwmon*"):
            if (h / "name").read_text().strip() in ("k10temp", "coretemp", "zenpower"):
                return int((h / "temp1_input").read_text()) / 1000
        return 0

    def cpu_usage(self):
        v = [int(x) for x in Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
        idle, total = v[3] + v[4], sum(v)
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if not prev or total == prev[1]:
            return 0
        return 100 * (1 - (idle - prev[0]) / (total - prev[1]))

    def read(self, mapping):
        g = self.gpu() if any(src in GPU_SOURCES for src in mapping.values()) else {}
        vals = {}
        for slot, src in mapping.items():
            try:
                if src in g:
                    vals[slot] = g[src]
                elif src == "cpu_temp":
                    vals[slot] = self.cpu_temp()
                elif src == "cpu_usage":
                    vals[slot] = self.cpu_usage()
                elif src == "zero":
                    vals[slot] = 0
                elif src.startswith("cmd:"):
                    vals[slot] = float(subprocess.run(src[4:], shell=True, capture_output=True, text=True,
                                                      timeout=5).stdout.split()[0])
                else:
                    vals[slot] = float(src)
            except Exception as e:                       # one bad source must not stop the feed
                print(f"{slot}={src}: {e}", file=sys.stderr)
                vals[slot] = 0
        return vals
