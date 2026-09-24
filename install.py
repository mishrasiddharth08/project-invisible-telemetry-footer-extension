from __future__ import annotations

import importlib
import shutil
import subprocess
import sys

TAG = "[PI-Telemetry]"


def have_module(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def pip_install(*args) -> bool:
    command = [sys.executable, "-m", "pip", "install", "--no-input", *args]
    try:
        return subprocess.call(command) == 0
    except Exception as error:
        print(f"{TAG}   pip failed ({error})")
        return False


def main() -> None:
    print(f"{TAG} PROJECT INVISIBLE - Telemetry Footer (install check)")

    if have_module("psutil"):
        print(f"{TAG}   psutil already present - nothing to install")
    else:
        print(f"{TAG}   installing psutil (CPU / RAM / SWAP probing)")
        pip_install("psutil>=5.9.0")

    if have_module("pynvml"):
        print(f"{TAG}   pynvml already present - nothing to install")
    elif shutil.which("nvidia-smi"):
        print(f"{TAG}   installing nvidia-ml-py (NVIDIA VRAM probing)")
        if not pip_install("nvidia-ml-py"):
            pip_install("pynvml")
    else:
        print(f"{TAG}   no NVIDIA driver detected - VRAM shows N/A (CPU / RAM / SWAP unaffected)")

    print(f"{TAG}   never installs torch / gradio / any model stack")


try:
    main()
except Exception as error:
    print(f"{TAG} install check soft-failed (extension still loads): {error}", file=sys.stderr)
