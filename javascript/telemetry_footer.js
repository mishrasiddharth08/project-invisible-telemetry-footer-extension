(function () {
  "use strict";

  const TAG = "[PI-Telemetry]";
  const STRIP_CLASS = "pi-telemetry-strip";
  const POLL_URL = "./pi-telemetry/snapshot";
  const CONFIG_URL = "./pi-telemetry/config";
  const MIN_POLL_MS = 250;
  const REQUEST_TIMEOUT_MS = 5000;

  // Where each position setting hangs the strip. Every anchor is inserted
  // *before* its host element, so no Forge markup is ever modified.
  const ANCHORS = {
    quicksettings: ["#quicksettings"],
    prompts: ["#txt2img_neg_prompt", "#img2img_neg_prompt"],
    footer: ["#footer"],
  };

  let strips = [];
  let started = false;
  let loggedActive = false;
  let warnedNoAnchor = false;
  let last = null;
  let localGpuIndex = null;
  let position = null;
  let inFlight = false;
  let ctrl = null;
  let requestTimeout = null;
  let requestSerial = 0;
  let failures = 0;
  let suspended = false;
  let connectionLost = false;
  let lastReceivedAt = null;
  let timer = null;
  let cssChecked = false;

  function num(value, fallback) {
    const n = Number(value);
    return isFinite(n) ? n : fallback;
  }

  function clamp(n, lo, hi) {
    return Math.min(hi, Math.max(lo, n));
  }

  function app() {
    try {
      return gradioApp();
    } catch (e) {
      return document;
    }
  }

  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmt(value, compact) {
    return Number(value).toFixed(compact ? 1 : 2);
  }

  /* ---------------------------------------------------------------- names */

  function tidyCpu(name) {
    return String(name || "")
      .replace(/\((R|TM|r|tm)\)/g, "")
      .replace(/\b\d+th Gen\b/gi, "")
      .replace(/\bIntel\b|\bAMD\b|\bCore\b|\bCPU\b|\bProcessor\b/gi, "")
      .replace(/@.*$/, "")
      .replace(/\s+/g, " ")
      .trim() || String(name || "").trim();
  }

  function tidyGpu(name) {
    return String(name || "")
      .replace(/\bNVIDIA\b|\bGeForce\b|\bAMD\b|\bRadeon\b/gi, "")
      .replace(/\s+/g, " ")
      .trim() || String(name || "").trim();
  }

  function cpuModel(cpu) {
    // No clock here: the live figure is rendered in the body next to the
    // percentage, so the identity line stays static and the speed moves.
    const bits = [tidyCpu(cpu.name)];
    const p = num(cpu.cores_physical, 0);
    const l = num(cpu.cores_logical, 0);
    if (p > 0 && l > 0) bits.push(p + "C/" + l + "T");
    return bits.filter(Boolean).join("  ·  ");
  }

  function clockText(cpu) {
    const mhz = num(cpu.mhz_now, 0) || num(cpu.mhz, 0);
    return mhz > 0 ? (mhz / 1000).toFixed(2) + " GHz" : "";
  }

  function temperatureText(value) {
    if (typeof value !== "number" || !isFinite(value) || value < -20 || value > 150) return "N/A";
    return value.toFixed(1) + " °C";
  }

  function ramModel(hw) {
    if (!hw) return "";
    const bits = [];
    if (hw.vendor) bits.push(hw.vendor);
    if (hw.part) bits.push(hw.part);
    if (hw.kind && num(hw.speed_mhz, 0) > 0) bits.push(hw.kind + "-" + hw.speed_mhz);
    else if (hw.kind) bits.push(hw.kind);
    else if (num(hw.speed_mhz, 0) > 0) bits.push(hw.speed_mhz + " MHz");
    if (num(hw.modules, 0) > 1 && num(hw.module_gb, 0) > 0) {
      bits.push(hw.modules + "×" + Math.round(hw.module_gb) + "GB");
    }
    return bits.join("  ·  ");
  }

  /* ---------------------------------------------------------------- paint */

  function tier(pct, amber, red) {
    if (pct >= red) return "crit";
    if (pct >= amber) return "warn";
    return "ok";
  }

  function cell(key, label, model, pct, detail, extraClass) {
    return (
      '<div class="pi-cell ' + (extraClass || "") + '" data-k="' + key + '">' +
      '<div class="pi-head">' +
      '<span class="pi-key">' + esc(label) + "</span>" +
      (model ? '<span class="pi-model">' + esc(model) + "</span>" : "") +
      "</div>" +
      '<div class="pi-body">' +
      '<span class="pi-val" data-v="' + key + '">' + Math.round(pct) + "</span>" +
      '<span class="pi-unit">%</span>' +
      // Always emitted, even when empty: paint() rewrites this every tick, so
      // the used/total figures track the percentage instead of freezing at
      // whatever they were when the markup was built.
      '<span class="pi-sub" data-s="' + key + '">' + esc(detail || "") + "</span>" +
      "</div>" +
      (key !== "swap" ? '<div class="pi-thermal"><span class="pi-thermal-label">' +
        (key.indexOf("gpu") === 0 ? "GPU" : key.toUpperCase()) + ' TEMP</span>' +
        '<span class="pi-temp pi-unavailable" data-t="' + key + '">N/A</span></div>' : "") +
      '<div class="pi-gauge"><i data-g="' + key + '"></i></div>' +
      "</div>"
    );
  }

  function memDetail(m, compact) {
    return (
      fmt(num(m.used_gb, 0), compact) + " / " + fmt(num(m.total_gb, 0), compact) +
      (compact ? " GB" : " GiB")
    );
  }

  function pickGpus(gpus, index) {
    if (!gpus || !gpus.length) return [];
    if (index === -1) return gpus;
    if (index >= 0 && index < gpus.length) return [gpus[index]];
    return [gpus[0]];
  }

  /* Build once per data shape change, then update the live figures together. */
  function buildHtml(s) {
    const cfg = s.cfg || {};
    const compact = cfg.compact !== false;
    const models = cfg.show_models !== false;
    const cells = [];

    if (cfg.show_cpu !== false && s.cpu) {
      cells.push(cell("cpu", "CPU", models ? cpuModel(s.cpu) : "", num(s.cpu.pct, 0), clockText(s.cpu)));
    }
    if (cfg.show_ram !== false && s.ram && num(s.ram.total_gb, 0) > 0) {
      cells.push(cell("ram", "RAM", models ? ramModel(s.ram_hw || cfg.ram_hw) : "",
        num(s.ram.pct, 0), memDetail(s.ram, compact)));
    }
    if (cfg.show_vram !== false) {
      const index = localGpuIndex !== null ? localGpuIndex : num(cfg.gpu_index, 0);
      const list = pickGpus(s.gpus || [], index);
      if (!list.length) {
        cells.push(
          '<div class="pi-cell pi-dim" data-k="gpu">' +
          '<div class="pi-head"><span class="pi-key">GPU</span></div>' +
          '<div class="pi-body"><span class="pi-sub">no CUDA device</span></div>' +
          '<div class="pi-gauge"><i data-g="gpu"></i></div></div>'
        );
      } else {
        list.forEach(function (g) {
          const key = "gpu" + g.index;
          const label = list.length > 1 ? "VRAM" + g.index : "VRAM";
          const detail = num(g.total_gb, 0) > 0 ? memDetail(g, compact) : "N/A";
          cells.push(cell(key, label, models ? tidyGpu(g.name) : "", num(g.pct, 0), detail, "pi-gpu"));
        });
      }
    }
    if (cfg.show_swap !== false && s.swap && s.swap.present && num(s.swap.total_gb, 0) > 0) {
      cells.push(cell("swap", "SWAP", "", num(s.swap.pct, 0), memDetail(s.swap, compact)));
    }

    let html = "";
    cells.forEach(function (c, i) {
      if (i) html += '<span class="pi-div"></span>';
      html += c;
    });
    if (s.busy) html += '<span class="pi-live" title="generation running">LIVE</span>';
    html += '<span class="pi-connection" data-pi-connection role="status" hidden></span>';
    return html;
  }

  /* Everything that moves, per cell: the percentage AND the text beside it.
     Both are rewritten on every tick so the GB figures, the CPU clock and the
     percentage can never drift out of sync with each other. */
  function readings(s) {
    const compact = (s.cfg || {}).compact !== false;
    const out = {};
    if (s.cpu) out.cpu = { pct: num(s.cpu.pct, 0), detail: clockText(s.cpu), temperature: s.cpu.temperature_c };
    if (s.ram && num(s.ram.total_gb, 0) > 0) {
      out.ram = { pct: num(s.ram.pct, 0), detail: memDetail(s.ram, compact), temperature: s.ram.temperature_c };
    }
    if (s.swap && s.swap.present) {
      out.swap = { pct: num(s.swap.pct, 0), detail: memDetail(s.swap, compact) };
    }
    (s.gpus || []).forEach(function (g) {
      out["gpu" + g.index] = {
        pct: num(g.pct, 0),
        detail: num(g.total_gb, 0) > 0 ? memDetail(g, compact) : "N/A",
        temperature: g.temperature_c,
      };
    });
    return out;
  }

  let shapeKey = "";

  function shapeOf(s) {
    const cfg = s.cfg || {};
    return [
      cfg.show_cpu, cfg.show_ram, cfg.show_vram, cfg.show_swap, cfg.show_models,
      cfg.compact, cfg.gpu_index, localGpuIndex, (s.gpus || []).length,
      s.busy, s.swap && s.swap.present,
    ].join("|");
  }

  // Schedule only after the previous request finishes. Hidden pages have no
  // polling timer; returning to the page immediately requests a fresh sample.
  function schedulePoll(delay) {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (suspended || document.hidden) return;
    timer = setTimeout(pump, delay);
  }

  function pump() {
    timer = null;
    if (suspended || document.hidden) return;
    maybeFetch();
  }

  function paint() {
    if (!last || !strips.length) return;

    const cfg = last.cfg || {};
    if (cfg.enabled === false) {
      strips.forEach(function (el) { el.style.display = "none"; });
      return;
    }
    strips.forEach(function (el) { el.style.display = ""; });

    const key = shapeOf(last);
    if (key !== shapeKey) {
      shapeKey = key;
      const html = buildHtml(last);
      strips.forEach(function (el) {
        el.innerHTML = html;
        if ((last.gpus || []).length > 1) {
          el.querySelectorAll(".pi-gpu").forEach(function (gpuCell) {
            gpuCell.classList.add("pi-click");
            gpuCell.addEventListener("click", onGpuCycle);
          });
        }
      });
    }

    const goal = readings(last);
    const amber = num(cfg.amber, 80);
    const red = num(cfg.red, 95);

    Object.keys(goal).forEach(function (k) {
      const want = goal[k].pct;
      const detail = goal[k].detail;
      const cls = "pi-" + tier(want, amber, red);
      strips.forEach(function (el) {
        const valEl = el.querySelector('[data-v="' + k + '"]');
        if (valEl) {
          const text = cfg.compact === false ? want.toFixed(1) : String(Math.round(want));
          if (valEl.textContent !== text) valEl.textContent = text;
          if (valEl.className !== "pi-val " + cls) valEl.className = "pi-val " + cls;
        }
        const subEl = el.querySelector('[data-s="' + k + '"]');
        if (subEl && subEl.textContent !== detail) subEl.textContent = detail;
        const tempEl = el.querySelector('[data-t="' + k + '"]');
        if (tempEl) {
          const temperature = temperatureText(goal[k].temperature);
          const component = k.indexOf("gpu") === 0 ? "gpu" : k;
          const defaults = component === "ram" ? [55, 70] : component === "cpu" ? [80, 95] : [80, 90];
          const warn = num(cfg["temp_" + component + "_warn"], defaults[0]);
          const crit = num(cfg["temp_" + component + "_crit"], defaults[1]);
          tempEl.className = "pi-temp " + (temperature === "N/A" ? "pi-unavailable" : "pi-" + tier(goal[k].temperature, warn, crit));
          if (tempEl.textContent !== temperature) tempEl.textContent = temperature;
          tempEl.title = temperature === "N/A"
            ? (component === "gpu" ? "GPU temperature unavailable from the current backend."
              : component.toUpperCase() + " temperature unavailable. Requires an existing Libre Hardware Monitor or Open Hardware Monitor WMI provider exposing a compatible sensor.")
            : (k === "cpu" ? "CPU package temperature (highest core if package unavailable)"
              : k === "ram" ? "Hottest reported RAM module" : "GPU core temperature (not VRAM junction)")
              + "; yellow at " + warn + " °C, red at " + crit + " °C (configurable alerts)";
        }
        const gEl = el.querySelector('[data-g="' + k + '"]');
        if (gEl) {
          gEl.style.width = clamp(want, 0, 100).toFixed(1) + "%";
          if (gEl.className !== cls) gEl.className = cls;
        }
      });
    });
    showConnectionState();
  }

  /* ---------------------------------------------------------------- data */

  function interval() {
    if (!last || !last.cfg) return 1000;
    if (last.cfg.enabled === false) return 5000;
    return clamp(num(last.busy ? last.cfg.busy_ms : last.cfg.idle_ms, 1000), MIN_POLL_MS, 60000);
  }

  function showConnectionState() {
    strips.forEach(function (el) {
      el.classList.toggle("pi-stale", connectionLost);
      let status = el.querySelector("[data-pi-connection]");
      if (!status && connectionLost) {
        status = document.createElement("span");
        status.className = "pi-connection";
        status.setAttribute("data-pi-connection", "");
        status.setAttribute("role", "status");
        el.appendChild(status);
      }
      if (!status) return;
      status.hidden = !connectionLost;
      status.textContent = connectionLost ? (last ? "Reconnecting — readings paused" : "Waiting for telemetry") : "";
      status.title = lastReceivedAt === null ? "" : "Last received: " + new Date(lastReceivedAt).toLocaleTimeString();
    });
  }

  // The deadline covers both the connection and JSON body. Promise.race also
  // recovers if a browser ignores abort; a late response cannot replace last.
  function maybeFetch() {
    if (inFlight || suspended || document.hidden) return;
    inFlight = true;
    const serial = ++requestSerial;
    ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const controller = ctrl;
    const options = { cache: "no-store" };
    if (ctrl) options.signal = ctrl.signal;
    let timeout;
    const deadline = new Promise(function (_, reject) {
      timeout = setTimeout(function () {
        if (controller) controller.abort();
        reject(new Error("telemetry request timed out"));
      }, REQUEST_TIMEOUT_MS);
      requestTimeout = timeout;
    });
    const response = Promise.resolve().then(function () {
      return fetch(POLL_URL, options);
    }).then(function (r) {
      if (r.ok) return r.json();
      throw new Error("http " + r.status);
    });

    Promise.race([response, deadline])
      .then(function (s) {
        if (serial !== requestSerial) return;
        if (!s || s.error || !s.cfg || !s.cpu || !s.ram || !Array.isArray(s.gpus)) {
          throw new Error("invalid telemetry snapshot");
        }
        last = s;
        lastReceivedAt = Date.now();
        failures = 0;
        connectionLost = false;
        if (position !== s.cfg.position) {
          position = s.cfg.position;
          remount();
        }
        updateTitle(s);
        paint();
      })
      .catch(function () {
        if (serial !== requestSerial) return;
        failures += 1;
        connectionLost = true;
        showConnectionState();
      })
      .finally(function () {
        clearTimeout(timeout);
        if (serial !== requestSerial) return;
        inFlight = false;
        ctrl = null;
        requestTimeout = null;
        schedulePoll(failures ? Math.min(30000, 1000 * Math.pow(2, Math.min(failures - 1, 5))) : interval());
      });
  }

  function pausePolling() {
    if (timer !== null) clearTimeout(timer);
    if (requestTimeout !== null) clearTimeout(requestTimeout);
    timer = null;
    requestTimeout = null;
    requestSerial += 1;
    if (ctrl) ctrl.abort();
    ctrl = null;
    inFlight = false;
  }

  let lastTitleAt = 0;

  function updateTitle(s) {
    // The tooltip is static hardware identity; rebuilding it at the poll rate
    // would be pure waste, so it refreshes about once a second.
    const now = performance.now();
    if (now - lastTitleAt < 1000) return;
    lastTitleAt = now;
    if (!strips.length) return;
    const cfg = s.cfg || {};
    const c = s.cpu || {};
    const lines = ["CPU  " + (c.name || "?") + "  ·  " + num(c.cores_physical, "?") +
      " cores / " + num(c.cores_logical, "?") + " threads"];
    const hw = s.ram_hw || cfg.ram_hw;
    const r = s.ram || {};
    if (num(r.total_gb, 0) > 0) {
      lines.push("RAM  " + (ramModel(hw) || "?") + "  ·  " +
        fmt(num(r.total_gb, 0), true) + " GB total");
    }
    (s.gpus || []).forEach(function (g) {
      lines.push("GPU" + g.index + "  " + (g.name || "?") +
        (g.cuda_cap ? "  ·  CUDA " + g.cuda_cap : "") +
        (num(g.total_gb, 0) > 0 ? "  ·  " + fmt(num(g.total_gb, 0), true) + " GB" : ""));
    });
    if (cfg.driver) lines.push("Driver  " + cfg.driver);
    const d = s.disk || {};
    if (num(d.total_gb, 0) > 0) {
      lines.push("Disk  " + (d.path || "") + "  ·  " + fmt(num(d.free_gb, 0), true) +
        " GB free of " + fmt(num(d.total_gb, 0), true));
    }
    let hint = "Click to copy a full system snapshot";
    if ((s.gpus || []).length > 1) hint += "  |  Click the GPU cell to switch card";
    lines.push("", hint);
    const text = lines.join("\n");
    strips.forEach(function (el) {
      if (el.title !== text) el.title = text;
    });
  }

  /* ---------------------------------------------------------------- copy */

  function buildCopyText(s) {
    const cfg = s.cfg || {};
    const bits = [];
    const c = s.cpu || {};
    bits.push("CPU " + (c.name || "?") + " " + Math.round(num(c.pct, 0)) + "%");
    const hw = s.ram_hw || cfg.ram_hw;
    const r = s.ram || {};
    if (num(r.total_gb, 0) > 0) {
      bits.push("RAM " + (ramModel(hw) ? ramModel(hw) + " " : "") +
        fmt(num(r.used_gb, 0), true) + "/" + fmt(num(r.total_gb, 0), true) + "GB (" +
        Math.round(num(r.pct, 0)) + "%)");
    }
    (s.gpus || []).forEach(function (g) {
      if (num(g.total_gb, 0) > 0) {
        bits.push("GPU" + g.index + " " + (g.name || "") + " " + fmt(num(g.used_gb, 0), true) +
          "/" + fmt(num(g.total_gb, 0), true) + "GB (" + Math.round(num(g.pct, 0)) + "%)");
      }
    });
    const sw = s.swap || {};
    if (sw.present && num(sw.total_gb, 0) > 0) {
      bits.push("SWAP " + fmt(num(sw.used_gb, 0), true) + "/" + fmt(num(sw.total_gb, 0), true) + "GB");
    }
    const d = s.disk || {};
    if (num(d.total_gb, 0) > 0) {
      bits.push("Disk " + fmt(num(d.free_gb, 0), true) + "GB free");
    }
    bits.push("PI-Telemetry " + new Date(num(s.ts, Date.now() / 1000) * 1000).toISOString());
    bits.push("Temperatures: CPU " + temperatureText(c.temperature_c) + ", RAM " + temperatureText(r.temperature_c));
    (s.gpus || []).forEach(function (g) {
      bits.push("GPU" + g.index + " core " + temperatureText(g.temperature_c));
    });
    return bits.join(" | ");
  }

  function legacyCopy(text) {
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      document.body.removeChild(area);
    } catch (e) {}
  }

  function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(function () { legacyCopy(text); });
        return;
      }
    } catch (e) {}
    legacyCopy(text);
  }

  function onCopy(e) {
    if (!last) return;
    copyText(buildCopyText(last));
    const el = e.currentTarget;
    el.classList.add("pi-copied");
    setTimeout(function () { el.classList.remove("pi-copied"); }, 450);
  }

  function saveGpuIndex(index) {
    try {
      fetch(CONFIG_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gpu_index: index }),
      })
        .then(function (r) { return r.json(); })
        .then(function (j) { if (j && j.ok) localGpuIndex = null; })
        .catch(function () {});
    } catch (e) {}
  }

  function onGpuCycle(e) {
    e.stopPropagation();
    e.preventDefault();
    if (!last || (last.gpus || []).length < 2) return;
    const cfg = last.cfg || {};
    const current = localGpuIndex !== null ? localGpuIndex : num(cfg.gpu_index, 0);
    let next = current + 1;
    if (next >= last.gpus.length) next = -1;
    localGpuIndex = next;
    shapeKey = "";
    paint();
    saveGpuIndex(next);
  }

  /* --------------------------------------------------------------- mount */

  function makeStrip() {
    const el = document.createElement("div");
    el.className = STRIP_CLASS;
    el.title = "PI Telemetry";
    el.addEventListener("click", onCopy);
    return el;
  }

  function anchorsFor(pos) {
    const selectors = ANCHORS[pos] || ANCHORS.quicksettings;
    const found = [];
    const root = app();
    selectors.forEach(function (sel) {
      let el = null;
      try { el = root.querySelector(sel); } catch (e) {}
      if (!el) { try { el = document.querySelector(sel); } catch (e) {} }
      // For the prompt position, hang off the row that wraps the textbox so the
      // strip spans the full prompt column rather than splitting the toolbar.
      if (el && pos === "prompts") {
        const row = el.closest(".prompt-container, .form, .gradio-row") || el;
        el = row.parentNode ? row : el;
      }
      if (el && el.parentNode) found.push(el);
    });
    return found;
  }

  function remount() {
    strips.forEach(function (el) {
      if (el.parentNode) el.parentNode.removeChild(el);
    });
    strips = [];
    shapeKey = "";
    ensure();
  }

  function ensure() {
    const pos = position || "quicksettings";
    const anchors = anchorsFor(pos);
    if (!anchors.length) {
      if (started && !warnedNoAnchor) {
        warnedNoAnchor = true;
        console.warn(TAG + " no anchor for position '" + pos +
          "' - strip not injected (soft-fail, UI unchanged)");
      }
      return false;
    }
    strips = strips.filter(function (el) { return el.isConnected; });
    if (strips.length === anchors.length) return true;

    strips.forEach(function (el) { if (el.parentNode) el.parentNode.removeChild(el); });
    strips = anchors.map(function (anchor) {
      const el = makeStrip();
      anchor.parentNode.insertBefore(el, anchor);
      return el;
    });
    shapeKey = "";
    warnedNoAnchor = false;
    if (started && !loggedActive) {
      loggedActive = true;
      console.info(TAG + " strip mounted at '" + pos + "' (" + strips.length + ")");
    }
    checkCssLoaded();
    paint();
    return true;
  }

  const FALLBACK_CSS_URL = "./file=extensions/project-invisible-telemetry-footer/style.css";

  function checkCssLoaded() {
    if (cssChecked || !strips.length) return;
    cssChecked = true;
    setTimeout(function () {
      const el = strips[0];
      if (!el || !el.isConnected) { cssChecked = false; return; }
      let loaded = "";
      try {
        loaded = getComputedStyle(el).getPropertyValue("--pi-loaded").trim();
      } catch (e) {}
      if (!loaded) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = FALLBACK_CSS_URL;
        document.head.appendChild(link);
        console.info(TAG + " style.css not served by host - linked it directly");
      }
    }, 500);
  }

  function boot() {
    ensure();
    if (!started) {
      started = true;
      schedulePoll(0);
    }
  }

  function fallbackWatch() {
    const iv = setInterval(function () {
      boot();
      if (strips.length) clearInterval(iv);
    }, 500);
    setTimeout(function () { clearInterval(iv); }, 60000);
  }

  try {
    if (typeof onUiLoaded === "function") onUiLoaded(boot);
    else fallbackWatch();
  } catch (e) {
    fallbackWatch();
  }

  try {
    if (typeof onUiUpdate === "function") onUiUpdate(function () { ensure(); });
  } catch (e) {}

  let obsTimer = null;
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) pausePolling();
    else if (started) schedulePoll(0);
  });
  window.addEventListener("pagehide", function () {
    suspended = true;
    pausePolling();
  });
  window.addEventListener("pageshow", function () {
    suspended = false;
    if (started) schedulePoll(0);
  });
  try {
    const observer = new MutationObserver(function () {
      if (strips.length && strips.every(function (el) { return el.isConnected; })) return;
      if (obsTimer) return;
      obsTimer = setTimeout(function () { obsTimer = null; ensure(); }, 150);
    });
    observer.observe(app(), { childList: true, subtree: true });
  } catch (e) {}
})();
