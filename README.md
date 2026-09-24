# PROJECT INVISIBLE — Telemetry Strip

Version **1.1.3** · MIT (this extension's code only)

A Forge Neo **invisible HUD**: one thin live instrument strip — CPU / RAM / VRAM / SWAP —
that names your actual hardware and colours itself green, amber or red as load climbs.
It looks like Forge shipped it. No new tab. No second window. No second venv. No System
Info page. It never appears in the Scripts dropdown and never changes how you generate.

```
 CPU  i7-13700K · 16C/24T     RAM  G.Skill · DDR5-5600 · 2×48GB    VRAM  RTX 5090           SWAP
  34 %  4.02 GHz               17 %  16.1 / 95.7 GB                 41 %  13.1 / 31.8 GB      0 %  0.0 / 36.0 GB
 ▔▔▔▔▁▁▁▁▁▁                   ▔▔▁▁▁▁▁▁▁▁                           ▔▔▔▔▁▁▁▁▁▁                ▁▁▁▁▁▁▁▁▁▁
```

Each cell carries the hardware's real identity, a live percentage, the used/total figure
and a hairline gauge that fills as it loads.

---

## 1. Install

1. Copy this folder to
   `<forge>\extensions\project-invisible-telemetry-footer`
   (so `install.py` sits directly inside it — not one level deeper).
2. **Restart Forge completely.** A "Reload UI" click is not enough; dependencies are
   only checked at startup.
3. Refresh the browser (F5).
4. The strip appears **above the checkpoint dropdowns** at the top of the page, on every
   tab. Move it in Settings — see §3.

First launch runs `install.py`, which installs **only** `psutil` (if missing) and
optionally `nvidia-ml-py` (only when an NVIDIA driver is present and pynvml is missing).
Never torch, never gradio, never any model stack. A second launch installs nothing that
is already there.

## 2. What it shows

| Cell | Identity line | Live reading |
|---|---|---|
| **CPU** | model · physical cores / threads | load % + **live clock** |
| **RAM** | kit vendor · type-speed · modules × size | used / total + % |
| **VRAM** | GPU card model | used / total VRAM + memory % |
| **SWAP** | — | used / total + % (hidden when swap is 0) |

RAM identity comes from SMBIOS (`Win32_PhysicalMemory` on Windows, `dmidecode` on Linux),
probed **once at startup** and cached — so `DDR5-5600`, `G.Skill`, `2×48GB` are read from
the modules themselves, not guessed. The full RAM part number is also shown when
available. Configured memory speed and installed capacity normally stay constant;
used memory and its percentage change. The VRAM percentage measures graphics memory
occupancy, not GPU processing utilization. CPU clock is an estimate from the Windows
performance counter; if unavailable, the existing base-clock fallback is used.

**Colours** apply to the number and its gauge:

| Tier | Default | Meaning |
|---|---|---|
| 🟢 Green | below 80 % | safe |
| 🟠 Amber | 80 % and up | alert |
| 🔴 Red | 95 % and up | danger (gauge glows) |

Both thresholds are configurable.

### Live temperatures

CPU, RAM and GPU cells include a temperature in degrees Celsius. GPU temperature
comes from the existing NVIDIA NVML connection on every shared snapshot (normally
250 ms); it is GPU core temperature, not graphics-memory junction temperature.
No CUDA workload is created. Backends without a temperature sensor show `N/A`.

On Windows, CPU and RAM temperatures are read from an **already running**
[Libre Hardware Monitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
or Open Hardware Monitor WMI provider. CPU package temperature is preferred, with
the highest CPU-core temperature as fallback; RAM shows the hottest explicitly
identified DIMM sensor. RAM temperature is available only if the provider exposes
module sensors. System/ACPI thermal zones and GPU memory sensors are not substituted.

The optional WMI query runs in the background about every two seconds, with a
three-second timeout and a 30-second retry when unavailable. Readings expire after
five seconds. Missing sensors, permissions or providers show `N/A`, never a guessed
zero. This extension does not install or start monitoring apps or privileged drivers.
Temperature badges use independent green/yellow/red alerts. Default yellow/red levels
are CPU 80/95 °C, GPU 80/90 °C and RAM 55/70 °C, adjustable in Settings.
These are display alerts, not manufacturer-certified safety limits. Missing readings
remain neutral gray. Hover a temperature for its source and configured thresholds.
CPU/RAM temperature support in this revision is Windows-specific.

The jelly-style strip uses rounded cards, static translucent highlights, larger
tabular numbers and compact temperature pills. No continuous pulse or backdrop blur
is needed; hardware names remain visible on smaller screens.

| Action | Result |
|---|---|
| Hover the strip | Full tooltip: CPU model, cores, RAM kit, every GPU + CUDA level, driver, disk free |
| Click the strip | Copies a one-line system snapshot to the clipboard |
| Click the **GPU** cell (multi-GPU) | Cycles GPU 0 → GPU 1 → … → all → back; remembered in `config.json` |
| `LIVE` pill | A generation is running |

## 3. Settings

Settings tab → **"Project Invisible Telemetry"**:

| Setting | Default | Meaning |
|---|---|---|
| Enabled | on | Master switch (off = strip hidden, UI untouched) |
| **Strip position** | Top | Radio: **Top** (above the checkpoint dropdowns) · **Middle** (between the positive and negative prompt boxes) · **Bottom** (just above the python / gradio version line) |
| Show CPU / RAM / VRAM / SWAP | on | Hide individual cells |
| Show hardware model names | on | Off = numbers only |
| Compact numbers | on | One decimal; off = two |
| Amber warning threshold % | 80 | Where green becomes amber |
| Red critical threshold % | 95 | Where amber becomes red |
| Idle refresh interval, ms | 1 | `1` = fastest supported sampling (250 ms); `0` = auto from the VRAM profile |
| Generate-time refresh interval, ms | 1 | Same, while generating |

Position changes apply **live** — the strip re-mounts itself without a page reload.

### About the 1 ms setting

An existing setting of `1` is accepted for compatibility and means the fastest supported
sampling: **250 ms**, up to four hardware snapshots per second. CPU load and effective
clock use the same measurement cycle, and concurrent clients share the cached snapshot.
This is not a guarantee of 1 ms sensor updates. RAM, VRAM and CPU figures, percentages,
gauges and threshold colours repaint together without interpolating the percentage.
Rounding can still hide small changes; detailed mode shows more decimal places.

Longer intervals are respected. `0` selects the automatic VRAM profile. The API exposes
the requested values separately from effective `idle_ms` / `busy_ms` and `sample_ms`.
Browser polling pauses when the tab is hidden; the background server sampler remains
active. No repeated animation loop is needed for the figures.

A request that stalls for five seconds is cancelled and retried with increasing delays
(one second up to 30 seconds). Old readings are dimmed and marked as paused until a
fresh snapshot arrives. Late responses cannot overwrite a newer reading.

Validation status for this update is recorded in [VALIDATION.md](VALIDATION.md).

## 4. If the strip is missing

1. Forge console: look for `[PI-Telemetry]`. `active | GET /pi-telemetry/snapshot …` means
   the server side is fine. `library load failed, HUD disabled` means a dependency
   problem — run `install.py` with the Forge venv python.
2. Browser console (F12): search `PI-Telemetry`. `strip mounted at 'quicksettings'` = good.
   `no anchor for position …` means a Forge update renamed that element — the extension
   soft-fails on purpose and never breaks the UI.
3. Settings → Extensions: confirm `project-invisible-telemetry-footer` is enabled.
4. Direct probe: open `http://127.0.0.1:7860/pi-telemetry/snapshot` — you should see JSON
   with `cpu`, `ram`, `ram_hw`, `swap`, `gpus`, `busy`, `cfg`.
5. Did you only click "Reload UI"? Restart Forge fully, then refresh the browser.

## 5. What it never does (guarantees)

- Never edits Forge core files. The strip is a **sibling inserted before** an existing
  element (`#quicksettings`, `#txt2img_neg_prompt`, or `#footer`); `modules/ui.py`,
  `footer.html`, `versions_html()` and `backend/memory_management` are untouched.
- No new top-level tab, no System Info tab, no second Gradio app, no new theme.
- Never appears in the Scripts dropdown; adds zero generator controls.
- **LoRA no-op: never scans `models/Lora/`, never classifies, applies or touches LoRAs,
  Extra Networks, checkpoints, VAE or text-encoder boxes.** Foreign LoRAs and every other
  PROJECT INVISIBLE engine are unaffected.
- Never monkey-patches UNet / attention / memory management; never wraps Generate; never
  allocates CUDA tensors; never calls `torch.cuda.empty_cache()`.
- Reads only already-public counters (`psutil`, NVML, `torch.cuda.mem_get_info`).
- No model weights, no downloads, no hash checks, one process, Forge venv only.
- **Never phones home.** "Telemetry" here means *your* hardware readings shown to *you*;
  nothing leaves the machine.

### One caveat worth knowing

`/pi-telemetry/snapshot` is registered on Forge's FastAPI app, which sits **outside**
gradio's `--gradio-auth`. On localhost that is irrelevant. If you run Forge with
`--listen` or `--share`, anyone who can reach the port can read your hardware summary
(CPU/GPU model, RAM totals, install path, free disk). Turn the extension off for public
exposure if that matters to you.

## 6. Fallback matrix

| Situation | Behaviour |
|---|---|
| `psutil` missing | `install.py` installs `psutil>=5.9.0` |
| NVML missing (NVIDIA present) | `install.py` installs `nvidia-ml-py`; runtime falls back to `torch.cuda.mem_get_info`, then `nvidia-smi` (0.4 s timeout, max one call per 750 ms), then `N/A` |
| AMD / CPU-only / MPS | CPU + RAM + SWAP stay live; GPU cell shows `no CUDA device` |
| RAM SMBIOS unreadable (no dmidecode, locked-down WMI) | Identity line omitted; the RAM meter still works |
| Anchor element renamed by a Forge update | Soft-fail: one console warning, strip hidden, UI fully functional |
| `onUiLoaded` / `onUiUpdate` missing | Independent DOM watcher re-inserts the strip |
| `style.css` not served | JS links the stylesheet directly |
| `requestAnimationFrame` unavailable | Polling and painting use timers and completed requests |
| Request stalls or returns an error | Five-second deadline, bounded retry delay, old readings marked as paused |
| Swap total is 0 | SWAP cell auto-hidden |
| Poll thread dies | The API endpoint refreshes inline instead of serving stale data |
| Multi-GPU | GPU 0 by default; click to cycle; per-GPU NVML handles, zero CUDA allocations |
| "Reload UI" clicked repeatedly | The poller is a per-process singleton — no thread is leaked per reload |

## 7. Files

```
project-invisible-telemetry-footer/
  install.py                      startup dependency check (psutil, optional nvidia-ml-py)
  requirements.txt                psutil>=5.9.0 (pynvml optional)
  metadata.ini                    extension metadata
  config.json                     per-machine hardware fingerprint + GPU choice (not published)
  README.md                       this file
  LICENSE                         MIT
  scripts/telemetry_footer.py     AlwaysVisible script: settings + on_app_started wiring
  javascript/telemetry_footer.js  mounts the strip, fetches, paints
  style.css                       .pi-telemetry-strip rules only
  lib/probe.py                    hardware autodetection + snapshot builder
  lib/poller.py                   thread-safe cache + interval policy + config.json owner
  api/routes.py                   GET /pi-telemetry/snapshot, POST /pi-telemetry/config
  sync.ps1                        mirrors LIVE -> archive + github, commits the archive
```

## 8. API

```
GET /pi-telemetry/snapshot
{ "ts": 0.0,
  "cpu":    { "pct": 0.0, "name": "", "mhz": 0, "cores_logical": 0, "cores_physical": 0 },
  "ram":    { "used_gb": 0.0, "total_gb": 0.0, "pct": 0.0 },
  "ram_hw": { "vendor": "", "part": "", "speed_mhz": 0, "kind": "", "modules": 0, "module_gb": 0.0 },
  "swap":   { "used_gb": 0.0, "total_gb": 0.0, "pct": 0.0, "present": true },
  "gpus":   [ { "index": 0, "name": "", "used_gb": 0.0, "total_gb": 0.0, "pct": 0.0, "cuda_cap": "" } ],
  "disk":   { "path": "", "free_gb": 0.0, "total_gb": 0.0 },
  "busy":   false,
  "cfg":    { "enabled": true, "position": "quicksettings", "show_cpu": true, "show_ram": true,
              "show_vram": true, "show_swap": true, "show_models": true, "compact": true,
              "amber": 80, "red": 95, "idle_ms": 250, "busy_ms": 250, "gpu_index": 0,
              "requested_idle_ms": 1, "requested_busy_ms": 1, "sample_ms": 250,
              "gpu_count": 1, "driver": "", "backend": "nvml", "cpu_mhz": 0, "ram_hw": {} } }

POST /pi-telemetry/config     body: { "gpu_index": 0 }   (0..N-1, or -1 = all GPUs)
```

## 9. Update / uninstall

- **Update:** replace the folder contents, restart Forge fully when other work is finished.
  Pinned at 1.1.3; never
  auto-updates, never phones home.
- **Uninstall:** delete the folder, restart Forge. Stock UI restored. Your settings live
  in Forge's own `config.json` under `pi_telemetry_*` and are harmless leftovers.
- **Sync:** after every stable change run
  `powershell -ExecutionPolicy Bypass -File "sync.ps1"` from the live folder. It mirrors
  into the archive (with git history) and the github tree.

## 10. Changelog

### 1.1.3

- Add live GPU core temperature via NVML, and optional CPU/RAM temperatures from
  existing Windows Libre/Open Hardware Monitor sensor providers.
- Poll optional sensors off the main snapshot path, expire old readings, and display
  unavailable values explicitly. Include temperature readings in copied snapshots.
- Add configurable green/yellow/red temperature badges and lightweight jelly styling.
- Thirteen offline tests pass, including temperature mapping, unavailable sensors,
  cache expiration and synchronized temperature repainting. Live validation is pending.

### 1.1.2

- Repaint percentages, amounts, clock, gauges and colours from the same snapshot.
  Removed percentage interpolation and the continuous animation loop.
- Share hardware reads across concurrent requests with a 250 ms minimum sampling
  interval. Sample CPU load and effective clock together.
- Cancel stalled requests, retry with backoff, flag paused readings, and discard late
  replies. Stop polling timers on hidden pages and resume on return.
- Label graphics memory as VRAM and include the RAM module part number.
- Add offline regression tests; live verification is deferred until training is idle.

### 1.1.1
- **Live CPU clock.** `psutil.cpu_freq()` is static on Windows (it reports the registry
  base clock — a 13700K reads a flat 3400 MHz idle or at full turbo), and
  `CallNtPowerInformation` is pinned the same way. The real figure now comes through PDH
  from the counter Task Manager uses: `base MHz × (% Processor Performance / 100)`.
  Reads ~4.0–5.2 GHz live, at 0.018 ms per sample.
- **Fixed: CPU load stuck at 0%.** psutil keys `cpu_percent`'s previous-call state by
  **thread id**, so the first call on any new thread measures a zero-length window and
  returns `0.0`. Forge answers each request on a fresh thread, so nearly every poll read
  zero. Load is now computed from `cpu_times()` deltas held by the Probe itself, not by
  whichever thread happens to ask. Verified: 0.2–3 % idle, 31–51 % under 12-process load.
- **Fixed: RAM and VRAM used/total never moved.** The GB figures were written into the
  markup once when the cell was built and only the percentage was repainted after that,
  so the text sat frozen while the percentage next to it changed. Every live figure is
  now rewritten on each tick, in step with its percentage.
- Sampling of CPU load, clock and disk is serialised so overlapping pollers cannot
  consume each other's measurement window.
- The tooltip rebuilds about once a second instead of at the poll rate.

### 1.1.0
- Hardware identity: CPU model + clock + core counts, RAM vendor / type / MHz / module
  layout via SMBIOS, GPU model — shown inline, not just in the tooltip.
- Green / amber / red tiers on every value and gauge, with a glow at critical.
- Selectable position: top (above the checkpoint dropdowns), middle (between the prompt
  boxes) or bottom (above the version line); applies live.
- Refresh rate down to 1 ms, with eased rendering so motion stays smooth.
- Redesigned strip: instrument-panel layout, hairline gauges, tabular figures.
- **Fixed:** every "Reload UI" leaked a live polling thread; the poller is now a
  per-process singleton.
- **Fixed:** CPU % read 0 at fast poll rates — `cpu_percent` now samples on its own
  300 ms window and the reading is reused in between. Disk gets a 5 s window.
- **Fixed:** the strip could freeze where `requestAnimationFrame` never fires; a timer
  now owns the data and rAF only smooths it.
- **Fixed:** `sync.ps1` mirrored `.git` away on every run, so the archive never held more
  than one commit. `.git` is now excluded, and the github tree is synced too.
- Polling pauses while the browser tab is hidden.

### 1.0.0
- Initial release: CPU / RAM / VRAM / SWAP strip above the version footer.

## License

MIT — see [LICENSE](LICENSE).
