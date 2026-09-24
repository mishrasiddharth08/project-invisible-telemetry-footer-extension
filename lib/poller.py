from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time

MODULE_PROBE = "pi_telemetry_lib_probe"

DEFAULT_CFG = {
    "enabled": True,
    "show_cpu": True,
    "show_ram": True,
    "show_vram": True,
    "show_swap": True,
    "compact": True,
    "show_models": True,
    "amber": 80,
    "red": 95,
    "temp_cpu_warn": 80,
    "temp_cpu_crit": 95,
    "temp_gpu_warn": 80,
    "temp_gpu_crit": 90,
    "temp_ram_warn": 55,
    "temp_ram_crit": 70,
    "poll_idle_ms": 0,
    "poll_busy_ms": 0,
    "position": "quicksettings",
}

POSITIONS = ("quicksettings", "prompts", "footer")

# CPU load needs a measurement window. Share one hardware sample across clients
# and clamp old 1 ms preferences to this reliable minimum.
SAMPLE_INTERVAL_MS = 250
THREAD_FLOOR_MS = SAMPLE_INTERVAL_MS


def load_module(name: str, path: str):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_probe():
    here = os.path.dirname(os.path.abspath(__file__))
    return load_module(MODULE_PROBE, os.path.join(here, "probe.py"))


class Poller:
    def __init__(self, probe=None, base_dir=None, config_path=None, opts_getter=None, busy_getter=None):
        self.probe = probe if probe is not None else load_probe().Probe()
        self.base_dir = os.path.abspath(
            base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self.config_path = config_path or os.path.join(self.base_dir, "config.json")
        self.opts_getter = opts_getter or (lambda: {})
        self.busy_getter = busy_getter or (lambda: False)
        self._cache = None
        self._cache_monotonic = 0.0
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.ensure_config()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="pi-telemetry-poller", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            self._stop.wait(self._next_interval())

    def _next_interval(self) -> float:
        cfg = self.effective_cfg()
        milliseconds = cfg["busy_ms"] if cfg["busy"] else cfg["idle_ms"]
        return max(THREAD_FLOOR_MS, milliseconds) / 1000.0

    def refresh(self) -> dict:
        # Recheck freshness after taking the sampling lock: concurrent browser
        # requests and the background poller must not duplicate hardware reads
        # or publish an older snapshot after a newer one.
        with self._refresh_lock:
            with self._lock:
                if (self._cache is not None and
                        time.monotonic() - self._cache_monotonic < SAMPLE_INTERVAL_MS / 1000.0):
                    return self._cache
            snapshot = self.probe.snapshot()
            snapshot["busy"] = bool(self.busy_getter())
            with self._lock:
                self._cache = snapshot
                self._cache_monotonic = time.monotonic()
            return snapshot

    def cached(self, max_age: float = 2.0):
        with self._lock:
            snapshot = self._cache
            age = time.monotonic() - self._cache_monotonic
        if snapshot is not None and age <= max_age:
            return snapshot
        try:
            return self.refresh()
        except Exception:
            return snapshot

    def effective_cfg(self) -> dict:
        try:
            values = self.opts_getter() or {}
        except Exception:
            values = {}
        cfg = {}
        for key, default in DEFAULT_CFG.items():
            value = values.get(key, default)
            if value is None:
                value = default
            if isinstance(default, bool):
                cfg[key] = bool(value)
            elif isinstance(default, int):
                try:
                    cfg[key] = int(value)
                except Exception:
                    cfg[key] = default
            else:
                cfg[key] = value
        poll_busy = cfg.pop("poll_busy_ms")
        poll_idle = cfg.pop("poll_idle_ms")
        profile_busy, profile_idle = self.probe.profile_intervals()
        busy_ms = poll_busy if poll_busy > 0 else profile_busy
        idle_ms = poll_idle if poll_idle > 0 else profile_idle
        cfg["requested_busy_ms"] = poll_busy
        cfg["requested_idle_ms"] = poll_idle
        cfg["sample_ms"] = SAMPLE_INTERVAL_MS
        for component in ('cpu', 'gpu', 'ram'):
            warn = 'temp_' + component + '_warn'
            crit = 'temp_' + component + '_crit'
            cfg[warn] = min(149, max(-20, cfg[warn]))
            cfg[crit] = min(150, max(cfg[warn] + 1, cfg[crit]))
        cfg["busy_ms"] = min(max(busy_ms, SAMPLE_INTERVAL_MS), 60000)
        cfg["idle_ms"] = min(max(idle_ms, SAMPLE_INTERVAL_MS), 60000)
        if cfg["position"] not in POSITIONS:
            cfg["position"] = DEFAULT_CFG["position"]
        cfg["busy"] = bool(self.busy_getter())
        config = self.read_config()
        ui = config.get("ui") if isinstance(config.get("ui"), dict) else {}
        try:
            gpu_index = int(ui.get("gpu_index", 0))
        except Exception:
            gpu_index = 0
        gpu_count = len(self.probe.gpu_static)
        if gpu_count == 0:
            gpu_index = 0
        elif not (0 <= gpu_index < gpu_count) and not (gpu_index == -1 and gpu_count > 1):
            gpu_index = 0
        cfg["gpu_index"] = gpu_index
        cfg["gpu_count"] = gpu_count
        cfg["driver"] = self.probe.driver
        cfg["backend"] = self.probe.vram_backend
        cfg["cpu_mhz"] = getattr(self.probe, "cpu_mhz", 0)
        cfg["ram_hw"] = dict(getattr(self.probe, "ram_hw", {}) or {})
        return cfg

    def snapshot_with_cfg(self) -> dict:
        cfg = self.effective_cfg()
        wanted_ms = cfg["busy_ms"] if cfg["busy"] else cfg["idle_ms"]
        snapshot = dict(self.cached(max_age=max(wanted_ms, 1) / 1000.0) or {})
        snapshot["cfg"] = cfg
        return snapshot

    def read_config(self) -> dict:
        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write_config(self, data: dict) -> bool:
        temporary = self.config_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temporary, self.config_path)
        return True

    def ensure_config(self) -> bool:
        with self._config_lock:
            config = self.read_config()
            fingerprint = self.probe.fingerprint()
            hardware = config.get("hardware")
            if config.get("version") == 1 and isinstance(hardware, dict) and hardware == fingerprint:
                return False
            ui = config.get("ui") if isinstance(config.get("ui"), dict) else {}
            try:
                gpu_index = int(ui.get("gpu_index", 0))
            except Exception:
                gpu_index = 0
            gpu_count = len(self.probe.gpu_static)
            if not (0 <= gpu_index < gpu_count) and not (gpu_index == -1 and gpu_count > 1):
                gpu_index = 0
            data = {
                "version": 1,
                "hardware": fingerprint,
                "probe": self.probe.static_info(),
                "ui": {"gpu_index": gpu_index},
            }
            try:
                self.write_config(data)
            except Exception as error:
                print(f"[PI-Telemetry] config.json write skipped (non-fatal): {error}")
                return False
            print("[PI-Telemetry] config.json updated (first launch or hardware change)")
            return True

    def set_gpu_index(self, index) -> bool:
        with self._config_lock:
            try:
                index = int(index)
            except Exception:
                return False
            gpu_count = len(self.probe.gpu_static)
            if not (0 <= index < gpu_count) and not (index == -1 and gpu_count > 1):
                return False
            config = self.read_config()
            if config.get("version") is None:
                config["version"] = 1
            ui = config.get("ui") if isinstance(config.get("ui"), dict) else {}
            ui["gpu_index"] = index
            config["ui"] = ui
            try:
                self.write_config(config)
                return True
            except Exception:
                return False
