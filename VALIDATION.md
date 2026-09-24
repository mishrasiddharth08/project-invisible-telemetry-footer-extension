# Telemetry 1.1.3 validation

## September 12 temperature update

- Follow-up: read-only WMI checks returned `Invalid namespace` for both supported
  providers; neither monitor process was found. CPU/RAM readings cannot be supplied
  through the current integration on this machine until a provider is available.
- Moved temperature badges into non-shrinking header slots with explicit TEMP labels,
  readable unavailable states, and more compact card padding. Top position retained.
  Superseded by the follow-up below.
- After a further report of hidden GPU temperature, put each temperature in a
  dedicated CPU TEMP / RAM TEMP / GPU TEMP row below the live readings. All six
  JavaScript tests pass, including explicit labelled-row markup and temperature
  repainting. The reported visual failure has not been reproduced; a screenshot
  and clarification of preview versus live Forge were requested.

- Added GPU temperature through the existing NVML connection and optional Windows
  CPU/RAM temperatures through an existing Libre/Open Hardware Monitor WMI provider.
- `node --test tests/telemetry_footer.test.cjs`: 6 passed, 0 failed.
- Forge venv Python `-B -m unittest discover -s tests -p test_telemetry.py`:
  7 passed, 0 failed. Both commands exited 0.
- Temperature tests also cover exact green/yellow/red boundaries, custom thresholds,
  ordered/clamped configuration and neutral unavailable badges.
- Updated jelly CSS and prepared an isolated HTML design preview with simulated data.
  Automatic local-file browser opening was blocked by browser policy; visual rendering
  has not been verified. No alternate browser-opening workaround was attempted.
- These are offline mocked tests; no hardware temperature queries, drivers, model
  imports, CUDA allocations, stress tests or process restarts were performed.
- No Libre/Open Hardware Monitor process was found in the lightweight process-name
  check. CPU/RAM sensor availability has not been confirmed; unsupported values show N/A.
- Live temperature verification remains pending until training is idle and Forge is
  restarted by the user to activate the updated modules, followed by a browser refresh.

## Previous 1.1.2 verification

Code prepared September 11, 2026. Python and JavaScript syntax checks passed during
implementation. The scheduled offline verification completed September 11, 2026,
at approximately 09:00 IST: all eight tests passed. No hardware probes, model imports,
CUDA allocations, network requests, stress tests or process restarts were performed.

## Recorded offline results

- `node --test tests/telemetry_footer.test.cjs`: 5 passed, 0 failed; exit code 0.
- Forge venv Python with `-B -m unittest discover -s tests -p test_telemetry.py`:
  3 passed, 0 failed; exit code 0.
- No regression failures were found in these suites, so no implementation changes
  were needed during the scheduled follow-up.
- Live verification remains pending: neither training being idle nor the updated
  telemetry modules being loaded has been positively confirmed. The offline results
  do not certify the running Forge UI or live hardware readings.

## Offline check commands

Run from this extension directory; both suites use fake readings and have no GPU,
network, model-loading or Forge-restart steps:

```
node --test tests/telemetry_footer.test.cjs
..\..\venv\Scripts\python.exe -B -m unittest discover -s tests -p test_telemetry.py
```

Coverage: percentage/amount/gauge synchronization, threshold colours, RAM part number,
VRAM label, 250 ms interval floor, timeout through JSON parsing, retry backoff, stale
status, late-response rejection, hidden-page pause/resume, shared concurrent samples,
and CPU clock/load sampling alignment.

## Live verification — pending until training is confirmed idle

- Do not stop, suspend, restart or reconfigure AI Toolkit, its jobs or its GPU.
- Do not load models, allocate CUDA memory, run stress tests or kill processes.
- Do not restart Forge automatically. Disk edits do not replace already loaded Python
  modules; browser refresh is also required to activate changed JavaScript.
- Once updated code is loaded and training is idle, observe the strip without artificial
  load: CPU clock/load, RAM used/total, VRAM used/total, colours and all three positions.
- Confirm requested intervals of 1 return effective intervals of 250 ms from the API;
  check hidden-tab pause and the connection-status display without changing Forge.
- Keep archive/publish synchronization deferred until regression checks pass. No live
  validation or full 1 ms hardware update rate has been claimed for this revision.

When training is finished, restart Forge fully and refresh its browser tab to activate
the changed Python modules and JavaScript. Then complete the live checks above. No
restart or browser refresh was performed automatically. Archive/publish synchronization
has not been performed during this follow-up.
