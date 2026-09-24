from __future__ import annotations

import json
import math
import os
import platform
import shutil
import subprocess
import threading
import time

GIB = 1024 ** 3
SMI_TIMEOUT = 0.4
SMI_MIN_INTERVAL = 0.75
WMI_TIMEOUT = 6.0

# CPU load and effective clock are sampled together over a meaningful window.
# The poller shares the resulting snapshot with every connected browser.
CPU_WINDOW = 0.25
CPU_CLOCK_WINDOW = CPU_WINDOW
DISK_WINDOW = 5.0


def gb(value) -> float:
    try:
        return float(value) / GIB
    except Exception:
        return 0.0


def import_optional(name: str):
    try:
        return __import__(name)
    except Exception:
        return None


def decode_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "latin-1"):
            try:
                return value.decode(encoding).strip()
            except Exception:
                continue
        return ""
    return str(value).strip()


def smi_query(fields: str, timeout: float = SMI_TIMEOUT):
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [exe, "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def cpu_name() -> str:
    system = platform.system()
    try:
        if system == "Windows":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return str(value).strip()
        if system == "Linux":
            with open("/proc/cpuinfo", "r", encoding="utf8", errors="ignore") as file:
                for line in file:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    name = platform.processor()
    if name:
        return name.strip()
    return platform.machine() or "CPU"



SMBIOS_MEMORY_TYPES = {
    20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5", 35: "LPDDR5",
}

VENDOR_ALIASES = (
    ("g skill", "G.Skill"), ("gskill", "G.Skill"), ("corsair", "Corsair"),
    ("kingston", "Kingston"), ("samsung", "Samsung"), ("hynix", "SK hynix"),
    ("micron", "Micron"), ("crucial", "Crucial"), ("adata", "ADATA"),
    ("teamgroup", "TEAMGROUP"), ("team group", "TEAMGROUP"), ("patriot", "Patriot"),
    ("kingmax", "Kingmax"), ("transcend", "Transcend"), ("pny", "PNY"),
)


def tidy_vendor(raw: str) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        return ""
    low = text.lower()
    for needle, pretty in VENDOR_ALIASES:
        if needle in low:
            return pretty
    for suffix in (" Intl", " International", " Inc", " Inc.", " Ltd", " Ltd.", " Technology", " Technologies"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def ram_hardware_windows():
    command = (
        "Get-CimInstance Win32_PhysicalMemory | "
        "Select-Object Manufacturer,PartNumber,Speed,ConfiguredClockSpeed,Capacity,SMBIOSMemoryType | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=WMI_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        data = json.loads(result.stdout)
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    sticks = [row for row in data if isinstance(row, dict)]
    if not sticks:
        return {}
    speeds = [int(row.get("ConfiguredClockSpeed") or row.get("Speed") or 0) for row in sticks]
    speeds = [value for value in speeds if value > 0]
    capacities = [int(row.get("Capacity") or 0) for row in sticks]
    kinds = [SMBIOS_MEMORY_TYPES.get(int(row.get("SMBIOSMemoryType") or 0), "") for row in sticks]
    return {
        "vendor": tidy_vendor(sticks[0].get("Manufacturer")),
        "part": " ".join(str(sticks[0].get("PartNumber") or "").split()),
        "speed_mhz": max(speeds) if speeds else 0,
        "kind": next((kind for kind in kinds if kind), ""),
        "modules": len(sticks),
        "module_gb": round(gb(capacities[0]), 0) if capacities and capacities[0] else 0.0,
    }


def ram_hardware_linux():
    exe = shutil.which("dmidecode")
    if exe is None:
        return {}
    try:
        result = subprocess.run(
            [exe, "-t", "memory"], capture_output=True, text=True, timeout=WMI_TIMEOUT
        )
    except Exception:
        return {}
    if result.returncode != 0:
        return {}
    sticks, current = [], {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Memory Device"):
            if current.get("size"):
                sticks.append(current)
            current = {}
            continue
        if ":" not in stripped:
            continue
        key, value = (part.strip() for part in stripped.split(":", 1))
        if value in ("", "Unknown", "Not Specified", "No Module Installed"):
            continue
        if key == "Size":
            current["size"] = value
        elif key == "Type":
            current["kind"] = value
        elif key == "Manufacturer":
            current["vendor"] = value
        elif key == "Part Number":
            current["part"] = value
        elif key in ("Configured Memory Speed", "Configured Clock Speed", "Speed"):
            digits = "".join(char for char in value if char.isdigit())
            if digits:
                current.setdefault("speed", int(digits))
    if current.get("size"):
        sticks.append(current)
    if not sticks:
        return {}
    speeds = [stick.get("speed", 0) for stick in sticks if stick.get("speed")]
    return {
        "vendor": tidy_vendor(sticks[0].get("vendor", "")),
        "part": sticks[0].get("part", ""),
        "speed_mhz": max(speeds) if speeds else 0,
        "kind": sticks[0].get("kind", ""),
        "modules": len(sticks),
        "module_gb": 0.0,
    }


def ram_hardware():
    try:
        if platform.system() == "Windows":
            return ram_hardware_windows()
        if platform.system() == "Linux":
            return ram_hardware_linux()
    except Exception:
        pass
    return {}



class CpuClock:
    """Live CPU clock in MHz.

    psutil.cpu_freq().current is useless on Windows: it reports the registry
    base clock and never moves (a 13700K sits at a flat 3400 whether idle or
    at full turbo). CallNtPowerInformation is pinned the same way on modern
    Windows. The figure Task Manager actually shows comes from a performance
    counter, so that is what is read here through PDH:

        live MHz = base MHz x (% Processor Performance / 100)

    Elsewhere psutil reports a real current frequency and is used directly.
    PDH rate counters need spacing between collections, so the reading is
    resampled on its own window and reused in between.
    """

    PDH_FMT_DOUBLE = 0x00000200
    COUNTER_PATH = r"\Processor Information(_Total)\% Processor Performance"

    def __init__(self, base_mhz: int = 0):
        self.base_mhz = int(base_mhz or 0)
        self.psutil = import_optional("psutil")
        self.lock = threading.Lock()
        self._value = 0
        self._sampled_at = 0.0
        self._pdh = None
        self._query = None
        self._counter = None
        if platform.system() == "Windows":
            self._init_pdh()

    def _init_pdh(self):
        try:
            import ctypes
            from ctypes import wintypes

            pdh = ctypes.WinDLL("pdh.dll")
            query = wintypes.LPVOID()
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
                return
            counter = wintypes.LPVOID()
            if pdh.PdhAddEnglishCounterW(query, self.COUNTER_PATH, 0, ctypes.byref(counter)) != 0:
                return
            pdh.PdhCollectQueryData(query)
            self._pdh = pdh
            self._query = query
            self._counter = counter
        except Exception:
            self._pdh = None
            self._query = None
            self._counter = None

    def _read_pdh(self) -> int:
        if self._pdh is None or self.base_mhz <= 0:
            return 0
        try:
            import ctypes
            from ctypes import wintypes

            class CounterValue(ctypes.Structure):
                _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return 0
            value = CounterValue()
            status = self._pdh.PdhGetFormattedCounterValue(
                self._counter, self.PDH_FMT_DOUBLE, None, ctypes.byref(value)
            )
            if status != 0:
                return 0
            percent = float(value.doubleValue)
            if percent <= 0:
                return 0
            return int(round(self.base_mhz * percent / 100.0))
        except Exception:
            return 0

    def _read_psutil(self) -> int:
        if self.psutil is None:
            return 0
        try:
            frequency = self.psutil.cpu_freq()
            if frequency is not None and frequency.current:
                return int(frequency.current)
        except Exception:
            pass
        return 0

    def read(self, force: bool = False) -> int:
        # PDH rate counters carry per-query state, so the window check and the
        # collection have to be atomic: two threads collecting back to back
        # would measure a near-zero interval and throw the reading away.
        with self.lock:
            now = time.monotonic()
            if not force and self._value and now - self._sampled_at < CPU_CLOCK_WINDOW:
                return self._value
            value = self._read_pdh() or self._read_psutil() or self.base_mhz
            self._value = int(value or 0)
            self._sampled_at = now
            return self._value


def temperature_value(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return round(value, 1) if math.isfinite(value) and -20 <= value <= 150 else None
    except (ValueError, TypeError):
        return None


class TemperatureReader:
    """Read an existing LHM/OHM WMI provider, without loading a sensor driver.

    Slow WMI queries run off the snapshot path. Missing providers are retried
    every 30 seconds; valid sensors every 2 seconds. Never reuse data past 5 s.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.sampled_at = 0.0
        self.next_read = 0.0
        self.running = False

    @staticmethod
    def select(rows):
        cpu, packages, ram = [], [], []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = temperature_value(row.get('Value'))
            if value is None:
                continue
            parent = str(row.get('Parent', '')).lower()
            name = str(row.get('Name', '')).lower()
            # GPU memory temperature must never be mistaken for DIMM temperature.
            if 'gpu' in parent:
                continue
            if parent.startswith(('/intelcpu/', '/amdcpu/')):
                cpu.append(value)
                if 'package' in name or 'tctl/tdie' in name:
                    packages.append(value)
            elif parent.startswith(('/ram/', '/memory/')) or 'dimm' in name:
                ram.append(value)
        return {'cpu': max(packages or cpu) if cpu else None,
                'ram': max(ram) if ram else None}

    def _query(self):
        command = (
            "$ErrorActionPreference='Stop'; "
            "foreach ($ns in @('root\\LibreHardwareMonitor','root\\OpenHardwareMonitor')) { "
            "try { $rows=@(Get-CimInstance -Namespace $ns -ClassName Sensor "
            "-Filter \"SensorType = 'Temperature'\" | Select-Object Name,Parent,Value); "
            "if ($rows.Count -gt 0) { ConvertTo-Json -InputObject $rows -Compress; exit 0 } "
            "} catch {} }; Write-Output '[]'"
        )
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, timeout=3.0,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode != 0:
            return {}
        rows = json.loads(result.stdout)
        if isinstance(rows, dict):
            rows = [rows]
        return self.select(rows) if isinstance(rows, list) else {}

    def _update(self):
        try:
            values = self._query()
        except Exception:
            values = {}
        with self.lock:
            self.values = values
            self.sampled_at = time.monotonic()
            self.next_read = self.sampled_at + (2 if any(v is not None for v in values.values()) else 30)
            self.running = False

    def read(self):
        if platform.system() != 'Windows':
            return {}
        with self.lock:
            now = time.monotonic()
            if not self.running and now >= self.next_read:
                self.running = True
                try:
                    threading.Thread(target=self._update, name='pi-temperature-read', daemon=True).start()
                except Exception:
                    self.running = False
                    self.next_read = now + 30
            return dict(self.values) if now - self.sampled_at <= 5 else {}


class Probe:
    def __init__(self, base_dir: str | None = None):
        self.lock = threading.RLock()
        self.psutil = import_optional("psutil")
        self.base_dir = os.path.abspath(
            base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self.cpu_name = cpu_name()
        self.cores_logical = 0
        self.cores_physical = 0
        self.cpu_mhz = 0
        self.ram_hw = {}
        self.nvml = None
        self.handles = []
        self.gpu_static = []
        self.driver = ""
        self.vram_backend = "none"
        self._smi_memory_cache = (0.0, [])
        self._cpu_cache = (0.0, 0.0)
        self._cpu_times_prev = None
        self._disk_cache = (0.0, None)
        self._prime()
        self._init_gpus()
        self.ram_hw = ram_hardware()
        self.clock = CpuClock(base_mhz=self.cpu_mhz)
        self._cpu_mhz_now = self.cpu_mhz
        self.temperature_reader = TemperatureReader()

    def gpu_temperature(self, index):
        # NVML is already initialized. No CUDA context, models or GPU workload.
        if self.nvml is None or index >= len(self.handles):
            return None
        try:
            return temperature_value(self.nvml.nvmlDeviceGetTemperature(
                self.handles[index], self.nvml.NVML_TEMPERATURE_GPU))
        except Exception:
            return None

    def _prime(self):
        if self.psutil is None:
            return
        try:
            self._cpu_times_prev = self._cpu_times_pair()
            self.cores_logical = self.psutil.cpu_count(logical=True) or 0
            self.cores_physical = self.psutil.cpu_count(logical=False) or 0
        except Exception:
            pass
        try:
            frequency = self.psutil.cpu_freq()
            if frequency is not None:
                self.cpu_mhz = int(frequency.max or frequency.current or 0)
        except Exception:
            pass

    def _nvml_capability(self, pynvml, handle) -> str:
        for function_name in (
            "nvmlDeviceGetComputeCapability",
            "nvmlDeviceGetCudaComputeCapability",
        ):
            function = getattr(pynvml, function_name, None)
            if function is None:
                continue
            try:
                value = function(handle)
                if isinstance(value, (tuple, list)) and len(value) >= 2:
                    return f"{int(value[0])}.{int(value[1])}"
                if isinstance(value, int):
                    return f"{value // 10}.{value % 10}"
            except Exception:
                continue
        return ""

    def _init_gpus(self):
        pynvml = import_optional("pynvml")
        if pynvml is not None:
            try:
                pynvml.nvmlInit()
                count = int(pynvml.nvmlDeviceGetCount())
                for index in range(count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                    memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    self.handles.append(handle)
                    self.gpu_static.append(
                        {
                            "index": index,
                            "name": decode_str(pynvml.nvmlDeviceGetName(handle)),
                            "total_gb": gb(memory.total),
                            "cuda_cap": self._nvml_capability(pynvml, handle),
                        }
                    )
                self.driver = decode_str(pynvml.nvmlSystemGetDriverVersion())
                self.nvml = pynvml
                self.vram_backend = "nvml"
                return
            except Exception:
                try:
                    pynvml.nvmlShutdown()
                except Exception:
                    pass
                self.handles = []
                self.gpu_static = []
                self.driver = ""
        if self._init_gpus_smi():
            return
        self._init_gpus_torch()

    def _init_gpus_smi(self) -> bool:
        text = smi_query("index,name,compute_cap,driver_version,memory.total")
        if not text:
            return False
        for line in text.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                index = int(parts[0])
                total_gb = gb(float(parts[4]) * 1024 * 1024)
            except ValueError:
                continue
            if not self.driver:
                self.driver = parts[3]
            self.gpu_static.append(
                {
                    "index": index,
                    "name": parts[1],
                    "total_gb": total_gb,
                    "cuda_cap": parts[2],
                }
            )
        self.vram_backend = "smi"
        return bool(self.gpu_static)

    def _init_gpus_torch(self) -> bool:
        try:
            import torch

            if not torch.cuda.is_available():
                return False
            properties = torch.cuda.get_device_properties(0)
            capability = torch.cuda.get_device_capability(0)
            self.gpu_static.append(
                {
                    "index": 0,
                    "name": str(torch.cuda.get_device_name(0)).strip(),
                    "total_gb": gb(properties.total_memory),
                    "cuda_cap": f"{capability[0]}.{capability[1]}",
                }
            )
            self.vram_backend = "torch"
            return True
        except Exception:
            return False

    def gpu_memory(self):
        if self.nvml is not None:
            result = []
            for index, handle in enumerate(self.handles):
                used = 0.0
                total = self.gpu_static[index]["total_gb"] if index < len(self.gpu_static) else 0.0
                try:
                    memory = self.nvml.nvmlDeviceGetMemoryInfo(handle)
                    used = gb(memory.used)
                    total = gb(memory.total) or total
                except Exception:
                    pass
                result.append((used, total))
            return result
        if self.vram_backend == "torch":
            try:
                import torch

                free, total = torch.cuda.mem_get_info(0)
                return [(gb(total) - gb(free), gb(total))]
            except Exception:
                return []
        if self.vram_backend == "smi":
            now = time.monotonic()
            if now - self._smi_memory_cache[0] < SMI_MIN_INTERVAL:
                return self._smi_memory_cache[1]
            text = smi_query("memory.used,memory.total")
            rows = []
            if text:
                for line in text.splitlines():
                    parts = [part.strip() for part in line.split(",")]
                    if len(parts) < 2:
                        continue
                    try:
                        rows.append(
                            (
                                gb(float(parts[0]) * 1024 * 1024),
                                gb(float(parts[1]) * 1024 * 1024),
                            )
                        )
                    except ValueError:
                        continue
            self._smi_memory_cache = (now, rows)
            return rows
        return []

    def _cpu_times_pair(self):
        """(idle, total) CPU seconds, or None."""
        try:
            times = self.psutil.cpu_times()
        except Exception:
            return None
        try:
            return float(times.idle), float(sum(times))
        except Exception:
            return None

    def _sample_cpu_pct(self) -> float:
        """System CPU load from cpu_times() deltas.

        NOT psutil.cpu_percent(): psutil keys its "previous call" state by
        THREAD ID (_last_cpu_times[tid]), so the first call on any new thread
        measures a zero-length window and returns 0.0. Forge answers each HTTP
        request on a fresh thread, so the meter read 0% almost every poll while
        only the long-lived poller thread ever saw a real figure. Deltas kept
        here belong to the Probe, not to whichever thread happens to ask.
        """
        previous = self._cpu_times_prev
        current = self._cpu_times_pair()
        if current is None:
            return self._cpu_cache[1]
        self._cpu_times_prev = current
        if previous is None:
            return self._cpu_cache[1]
        idle_delta = current[0] - previous[0]
        total_delta = current[1] - previous[1]
        if total_delta <= 0:
            return self._cpu_cache[1]
        return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))

    def disk_usage(self) -> dict:
        with self.lock:
            stamp, cached = self._disk_cache
            now = time.monotonic()
            if cached is not None and now - stamp < DISK_WINDOW:
                return cached
        result = {"path": self.base_dir, "free_gb": 0.0, "total_gb": 0.0}
        if self.psutil is None:
            self._disk_cache = (now, result)
            return result
        try:
            usage = self.psutil.disk_usage(self.base_dir)
            result["free_gb"] = round(gb(usage.free), 1)
            result["total_gb"] = round(gb(usage.total), 1)
        except Exception:
            pass
        self._disk_cache = (now, result)
        return result

    def static_info(self) -> dict:
        ram_total_gb = 0.0
        swap_total_gb = 0.0
        if self.psutil is not None:
            try:
                ram_total_gb = gb(self.psutil.virtual_memory().total)
                swap_total_gb = gb(self.psutil.swap_memory().total)
            except Exception:
                pass
        return {
            "cpu_name": self.cpu_name,
            "cores_logical": self.cores_logical,
            "cores_physical": self.cores_physical,
            "cpu_mhz": self.cpu_mhz,
            "ram_total_gb": round(ram_total_gb, 2),
            "ram_hw": dict(self.ram_hw or {}),
            "swap_total_gb": round(swap_total_gb, 2),
            "driver": self.driver,
            "vram_backend": self.vram_backend,
            "gpus": [
                {
                    "index": gpu["index"],
                    "name": gpu["name"],
                    "total_gb": round(gpu["total_gb"], 2),
                    "cuda_cap": gpu["cuda_cap"],
                }
                for gpu in self.gpu_static
            ],
        }

    def fingerprint(self) -> dict:
        info = self.static_info()
        return {
            "gpu_count": len(info["gpus"]),
            "gpu_names": [gpu["name"] for gpu in info["gpus"]],
            "gpu_totals": [gpu["total_gb"] for gpu in info["gpus"]],
            "driver": info["driver"],
            "ram_total_gb": info["ram_total_gb"],
            "cpu_name": info["cpu_name"],
        }

    def profile_intervals(self):
        totals = [gpu["total_gb"] for gpu in self.gpu_static if gpu["total_gb"] > 0]
        vram_gb = max(totals) if totals else 0.0
        if vram_gb <= 0:
            return 1000, 2500
        if vram_gb <= 12:
            return 750, 2000
        if vram_gb <= 16:
            return 500, 2000
        return 500, 2500

    def snapshot(self) -> dict:
        cpu_pct = 0.0
        cpu_mhz_now = self._cpu_mhz_now
        ram = {"used_gb": 0.0, "total_gb": 0.0, "pct": 0.0}
        swap = {"used_gb": 0.0, "total_gb": 0.0, "pct": 0.0, "present": False}
        if self.psutil is not None:
            # The window check and the sample must be atomic: the poller
            # thread and every inline refresh on the API path land here, and
            # two overlapping samples would consume each other's delta.
            with self.lock:
                sampled_at, previous = self._cpu_cache
                now = time.monotonic()
                if now - sampled_at >= CPU_WINDOW:
                    cpu_pct = self._sample_cpu_pct()
                    self._cpu_mhz_now = self.clock.read(force=True)
                    self._cpu_cache = (now, cpu_pct)
                else:
                    cpu_pct = previous
                cpu_mhz_now = self._cpu_mhz_now
            try:
                memory = self.psutil.virtual_memory()
                ram = {
                    "used_gb": round(gb(memory.total - memory.available), 2),
                    "total_gb": round(gb(memory.total), 2),
                    "pct": round(float(memory.percent), 1),
                }
            except Exception:
                pass
            try:
                swap_memory = self.psutil.swap_memory()
                swap = {
                    "used_gb": round(gb(swap_memory.used), 2),
                    "total_gb": round(gb(swap_memory.total), 2),
                    "pct": round(float(swap_memory.percent), 1),
                    "present": swap_memory.total > 0,
                }
            except Exception:
                pass
        memories = self.gpu_memory()
        reader = getattr(self, 'temperature_reader', None)
        temperatures = reader.read() if reader is not None else {}
        ram['temperature_c'] = temperatures.get('ram')
        gpus = []
        for index, gpu in enumerate(self.gpu_static):
            used = 0.0
            total = gpu["total_gb"]
            if index < len(memories):
                used, total = memories[index]
                total = total or gpu["total_gb"]
            pct = round(used / total * 100, 1) if total > 0 else 0.0
            gpus.append(
                {
                    "index": gpu["index"],
                    "name": gpu["name"],
                    "used_gb": round(used, 2),
                    "total_gb": round(total, 2),
                    "pct": pct,
                    "temperature_c": self.gpu_temperature(index),
                    "cuda_cap": gpu["cuda_cap"],
                }
            )
        return {
            "ts": round(time.time(), 3),
            "cpu": {
                "pct": round(cpu_pct, 1),
                "name": self.cpu_name,
                "mhz": self.cpu_mhz,
                "mhz_now": cpu_mhz_now,
                "temperature_c": temperatures.get('cpu'),
                "cores_logical": self.cores_logical,
                "cores_physical": self.cores_physical,
            },
            "ram": ram,
            "ram_hw": dict(self.ram_hw or {}),
            "swap": swap,
            "gpus": gpus,
            "disk": self.disk_usage(),
        }
