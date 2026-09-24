from __future__ import annotations

import importlib.util
import os
import sys

import gradio as gr

import modules.scripts as scripts
import modules.script_callbacks as script_callbacks
import modules.shared as shared

TAG = "[PI-Telemetry]"
EXTENSION_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(EXTENSION_ROOT, "config.json")
SECTION = ("pi_telemetry_footer", "Project Invisible Telemetry")

POSITION_CHOICES = [
    "Top — above the checkpoint dropdowns",
    "Middle — between the positive and negative prompt boxes",
    "Bottom — just above the python / gradio version line",
]
POSITION_VALUES = {
    POSITION_CHOICES[0]: "quicksettings",
    POSITION_CHOICES[1]: "prompts",
    POSITION_CHOICES[2]: "footer",
}

OPTION_MAP = {
    "pi_telemetry_enabled": "enabled",
    "pi_telemetry_position": "position",
    "pi_telemetry_show_cpu": "show_cpu",
    "pi_telemetry_show_ram": "show_ram",
    "pi_telemetry_show_vram": "show_vram",
    "pi_telemetry_show_swap": "show_swap",
    "pi_telemetry_show_models": "show_models",
    "pi_telemetry_compact": "compact",
    "pi_telemetry_warn_pct": "amber",
    "pi_telemetry_crit_pct": "red",
    "pi_telemetry_poll_idle_ms": "poll_idle_ms",
    "pi_telemetry_poll_busy_ms": "poll_busy_ms",
}

for _component in ('cpu', 'gpu', 'ram'):
    for _level in ('warn', 'crit'):
        _key = 'temp_' + _component + '_' + _level
        OPTION_MAP['pi_telemetry_' + _key] = _key

_probe_module = None
_poller_module = None
_routes_module = None
_poller = None


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


def _opts_getter():
    values = {}
    try:
        for option_key, cfg_key in OPTION_MAP.items():
            value = getattr(shared.opts, option_key, None)
            if cfg_key == "position":
                value = POSITION_VALUES.get(value, value)
            values[cfg_key] = value
    except Exception:
        return {}
    return values


def _busy_getter():
    try:
        state = shared.state
        return int(getattr(state, "job_count", 0)) != 0
    except Exception:
        return False


def _register_options():
    try:
        shared.opts.add_option(
            "pi_telemetry_enabled", shared.OptionInfo(True, "Enabled", section=SECTION)
        )
        shared.opts.add_option(
            "pi_telemetry_position",
            shared.OptionInfo(
                POSITION_CHOICES[0],
                "Strip position",
                gr.Radio,
                {"choices": POSITION_CHOICES},
                section=SECTION,
            ),
        )
        shared.opts.add_option(
            "pi_telemetry_show_cpu", shared.OptionInfo(True, "Show CPU meter", section=SECTION)
        )
        shared.opts.add_option(
            "pi_telemetry_show_ram", shared.OptionInfo(True, "Show RAM meter", section=SECTION)
        )
        shared.opts.add_option(
            "pi_telemetry_show_vram", shared.OptionInfo(True, "Show VRAM meter", section=SECTION)
        )
        shared.opts.add_option(
            "pi_telemetry_show_swap",
            shared.OptionInfo(True, "Show SWAP meter (auto-hidden when swap total is 0)", section=SECTION),
        )
        shared.opts.add_option(
            "pi_telemetry_show_models",
            shared.OptionInfo(True, "Show hardware model names (CPU / RAM kit / GPU)", section=SECTION),
        )
        shared.opts.add_option(
            "pi_telemetry_compact",
            shared.OptionInfo(True, "Compact numbers (one decimal). Off = detailed (two decimals)", section=SECTION),
        )
        shared.opts.add_option(
            "pi_telemetry_warn_pct", shared.OptionInfo(80, "Amber warning threshold %", section=SECTION)
        )
        shared.opts.add_option(
            "pi_telemetry_crit_pct", shared.OptionInfo(95, "Red critical threshold %", section=SECTION)
        )
        shared.opts.add_option(
            "pi_telemetry_poll_idle_ms",
            shared.OptionInfo(
                1,
                "Idle refresh interval, ms (250 minimum; 1 = fastest reliable sampling; 0 = auto)",
                section=SECTION,
            ),
        )
        shared.opts.add_option(
            "pi_telemetry_poll_busy_ms",
            shared.OptionInfo(
                1,
                "Generate-time refresh interval, ms (250 minimum; 1 = fastest reliable sampling; 0 = auto)",
                section=SECTION,
            ),
        )
        for component, warn, crit in (('cpu', 80, 95), ('gpu', 80, 90), ('ram', 55, 70)):
            for level, default, label in (('warn', warn, 'Yellow alert'), ('crit', crit, 'Red alert')):
                shared.opts.add_option(
                    'pi_telemetry_temp_' + component + '_' + level,
                    shared.OptionInfo(default, component.upper() + ' temperature: ' + label + ' (°C)', section=SECTION),
                )
        return True
    except Exception as error:
        print(f"{TAG} settings registration failed (non-fatal): {error}")
        return False


def _ram_summary(probe) -> str:
    ram = getattr(probe, "ram_hw", {}) or {}
    kind = ram.get("kind", "")
    speed = ram.get("speed_mhz", 0)
    parts = [ram.get("vendor", "")]
    if kind and speed:
        parts.append(f"{kind}-{speed}")
    elif kind:
        parts.append(kind)
    return " ".join(part for part in parts if part)


def _load():
    """Build the poller once per process.

    Forge re-executes this file on every "Reload UI" (load_scripts -> exec_module),
    so constructing on import leaks a live polling thread per reload. The lib
    modules are cached in sys.modules and survive reloads, so the instance is
    parked on the poller module and reused.
    """
    global _probe_module, _poller_module, _routes_module, _poller
    try:
        _probe_module = load_module(
            "pi_telemetry_lib_probe", os.path.join(EXTENSION_ROOT, "lib", "probe.py")
        )
        _poller_module = load_module(
            "pi_telemetry_lib_poller", os.path.join(EXTENSION_ROOT, "lib", "poller.py")
        )
        _routes_module = load_module(
            "pi_telemetry_api_routes", os.path.join(EXTENSION_ROOT, "api", "routes.py")
        )
        existing = getattr(_poller_module, "_PI_SINGLETON", None)
        if existing is not None:
            _poller = existing
            return
        probe = _probe_module.Probe(base_dir=EXTENSION_ROOT)
        _poller = _poller_module.Poller(
            probe=probe,
            base_dir=EXTENSION_ROOT,
            config_path=CONFIG_PATH,
            opts_getter=_opts_getter,
            busy_getter=_busy_getter,
        )
        _poller_module._PI_SINGLETON = _poller
    except Exception as error:
        print(f"{TAG} library load failed, HUD disabled (non-fatal): {error}")
        _poller = None


def _on_app_started(demo, app):
    try:
        if _poller is None:
            print(f"{TAG} poller unavailable (library load failed earlier); HUD stays off")
            return
        _poller.start()
        _routes_module.register(app, _poller)
        ram_text = _ram_summary(_poller.probe)
        print(
            f"{TAG} active | GET /pi-telemetry/snapshot"
            f" | vram backend: {_poller.probe.vram_backend}"
            f" | gpus: {len(_poller.probe.gpu_static)}"
            + (f" | ram: {ram_text}" if ram_text else "")
        )
    except Exception as error:
        print(f"{TAG} on_app_started soft-fail (UI unaffected): {error}")


_register_options()
_load()

try:
    script_callbacks.on_app_started(_on_app_started)
except Exception as error:
    print(f"{TAG} on_app_started registration failed (non-fatal): {error}")


class Script(scripts.Script):
    create_group = False

    def title(self):
        return "Telemetry Footer"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        return []

    def run(self, p, *args):
        return None
