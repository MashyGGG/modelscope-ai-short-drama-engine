import csv
import io
import shutil
import subprocess
import threading

import psutil


def snapshot():
    memory = psutil.virtual_memory()
    result = {
        "ram_used_mb": memory.used / 2**20,
        "ram_available_mb": memory.available / 2**20,
        "process_rss_mb": psutil.Process().memory_info().rss / 2**20,
        "gpus": [],
    }
    if shutil.which("nvidia-smi"):
        command = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if process.returncode:
            result["gpu_error"] = process.stderr.strip()
        else:
            for row in csv.reader(io.StringIO(process.stdout)):
                result["gpus"].append(
                    {
                        "index": int(row[0]),
                        "name": row[1].strip(),
                        "total_mb": float(row[2]),
                        "used_mb": float(row[3]),
                        "free_mb": float(row[4]),
                    }
                )
    return result


class ResourceMonitor:
    def __init__(self, interval=1):
        self.interval = interval
        self.stop_event = threading.Event()
        self.peak_vram_mb = 0
        self.peak_ram_mb = 0
        self.samples = 0
        self.errors = []

    def sample(self):
        try:
            data = snapshot()
            self.peak_ram_mb = max(self.peak_ram_mb, data["ram_used_mb"])
            self.peak_vram_mb = max(self.peak_vram_mb, max((gpu["used_mb"] for gpu in data["gpus"]), default=0))
            self.samples += 1
        except Exception as exc:
            self.errors.append(str(exc))

    def _run(self):
        while not self.stop_event.wait(self.interval):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join(timeout=15)
        self.sample()

    def report(self):
        return {
            "gpu_peak_vram_mb": self.peak_vram_mb,
            "system_peak_ram_mb": self.peak_ram_mb,
            "samples": self.samples,
            "monitor_errors": self.errors,
        }
