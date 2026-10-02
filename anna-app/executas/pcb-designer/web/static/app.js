// PCB Designer AI Agent — Dashboard JavaScript

const API = "/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  return resp.json();
}

function $(id) { return document.getElementById(id); }

function setBadge(elId, text, cls = "idle") {
  const el = $(elId);
  el.textContent = text;
  el.className = "badge " + cls;
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, t => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[t]));
}

// ── Status check ─────────────────────────────────────────────────────────────

async function checkStatus() {
  try {
    const data = await fetchJSON(`${API}/status`);
    setBadge("pcbnew-badge",
      data.pcbnew_available ? "Available ✓" : "Not installed",
      data.pcbnew_available ? "ok" : "warning");
    setBadge("provider-badge",
      data.default_provider,
      "ok");
  } catch (e) {
    setBadge("pcbnew-badge", "Error", "error");
    setBadge("provider-badge", "Error", "error");
  }

  // Fetch provider metrics
  try {
    const metrics = await fetchJSON(`${API}/providers/metrics`);
    if (metrics.providers && Object.keys(metrics.providers).length > 0) {
      const grid = $("metrics-grid");
      const items = Object.entries(metrics.providers).map(([name, s]) => `
        <div class="metric-card">
          <div class="metric-name">${name}</div>
          <div class="metric-value">${s.calls}</div>
          <div style="color:var(--text-muted);font-size:0.75rem">
            ${s.successes}✓ ${s.errors}✗ · ${Math.round(s.success_rate*100)}% success
          </div>
        </div>
      `);
      grid.innerHTML = items.join("");
      $("metrics-section").style.display = "block";
    }
  } catch (e) {
    // Metrics not available (no tracker) — silently skip
  }
}

// ── BOM rendering ─────────────────────────────────────────────────────────────

function renderBom(bom) {
  const tbody = $("bom-tbody");
  const countEl = $("bom-count");
  countEl.textContent = bom.length;

  if (bom.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No components generated.</td></tr>`;
    return;
  }

  tbody.innerHTML = bom.map(c => {
    const cat = c.category || "unknown";
    const ref = c.ref || "?";
    return `
      <tr>
        <td><strong>${escapeHtml(ref)}</strong></td>
        <td><span class="category-${cat}">${escapeHtml(cat)}</span></td>
        <td>${escapeHtml(c.mpn || "?")}</td>
        <td>${escapeHtml(c.package || "?")}</td>
        <td><code style="font-size:0.75rem">${escapeHtml(c.footprint || "?")}</code></td>
      </tr>
    `;
  }).join("");
}

// ── File downloads ────────────────────────────────────────────────────────────

function renderDownloads(result) {
  const section = $("download-section");
  const links = $("file-links");

  let items = [];
  const files = result.result?.files || {};

  if (result.result?.pcb_error) {
    items.push({ name: "board.kicad_pcb", path: files["schematic.kicad_sch"] ? null : null });
  }

  // Always show BOM
  items.push({ name: "BOM (bom.json)", path: "bom.json" });
  items.push({ name: "Schematic (schematic.kicad_sch)", path: "schematic.kicad_sch" });
  items.push({ name: "PCB (board.kicad_pcb)", path: "board.kicad_pcb" });

  links.innerHTML = items.map(item => `
    <a class="file-link" href="${API}/download/${item.path}" target="_blank">
      📄 ${item.name}
    </a>
  `).join("");
  section.style.display = "block";
}

// ── Run pipeline ─�─────────────────────────────────────────────────────────────

function logProgress(msg) {
  const logEl = $("progress-log");
  if (logEl) {
    logEl.textContent += msg + "\n";
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function setProgress(pct) {
  const bar = $("progress-bar");
  if (bar) bar.style.width = `${pct}%`;
}

function showProgress(show) {
  $("progress-section").style.display = show ? "block" : "none";
  $("results-section").style.display = show ? "none" : "block";
  if (show) {
    $("progress-log").textContent = "";
    setProgress(0);
  }
}

async function runPipeline() {
  const description = $("prompt-input").value.trim() || $("prompt-input").placeholder;
  const provider = $("provider-select").value;
  const btn = $("run-btn");

  btn.disabled = true;
  setBadge("pipeline-badge", "Running", "running");
  showProgress(true);

  logProgress(`[pipeline] Starting design for: ${description}`);
  setProgress(10);

  try {
    const startResp = await fetchJSON(`${API}/design`, {
      method: "POST",
      body: JSON.stringify({ description, provider }),
    });

    if (startResp.error) {
      throw new Error(startResp.error);
    }

    const taskId = startResp.task_id;
    logProgress(`[pipeline] Task ID: ${taskId}`);
    logProgress(`[pipeline] Waiting for results... (this may take 10-30 seconds)`);
    setProgress(30);

    // Poll for results
    let attempts = 0;
    const maxAttempts = 120; // 120 × 2.5s = 5 min max

    while (attempts < maxAttempts) {
      await new Promise(r => setTimeout(r, 2500));
      attempts++;

      const taskResp = await fetchJSON(`${API}/task/${taskId}`);

      if (taskResp.status === "done") {
        setProgress(100);
        logProgress("[pipeline] ✅ Pipeline complete!");

        const result = taskResp.result || {};
        logProgress(`[pipeline] Components: ${result.bom ? result.bom.length : 0}`);
        logProgress(`[pipeline] PCB error: ${result.pcb_error || "none"}`);

        // Show BOM
        if (result.bom && Array.isArray(result.bom)) {
          renderBom(result.bom);
        }

        // Show downloads
        renderDownloads(taskResp);

        // Show report
        if (result.analysis_report) {
          $("report-content").textContent = result.analysis_report;
          $("report-section").style.display = "block";
        }

        $("results-section").style.display = "block";
        setBadge("pipeline-badge", "Complete", "success");
        showProgress(false);
        break;

      } else if (taskResp.status === "error") {
        setProgress(100);
        logProgress(`[pipeline] ❌ Error: ${taskResp.error}`);
        setBadge("pipeline-badge", "Error", "error");
        $("results-section").innerHTML =
          `<div class="card"><h2 style="color:var(--red)">Pipeline Error</h2>` +
          `<pre style="color:var(--red); white-space:pre-wrap;">${escapeHtml(taskResp.error)}</pre></div>`;
        $("results-section").style.display = "block";
        showProgress(false);
        break;
      } else {
        logProgress(`[pipeline] Still running... (${attempts * 2.5}s elapsed)`);
        setProgress(Math.min(30 + attempts * 2, 90));
      }
    }

    if (attempts >= maxAttempts) {
      logProgress("[pipeline] ⚠ Timeout waiting for results.");
      setBadge("pipeline-badge", "Timeout", "error");
    }

  } catch (err) {
    logProgress(`[pipeline] ❌ ${err.message}`);
    setBadge("pipeline-badge", "Error", "error");
    showProgress(false);
    $("results-section").innerHTML =
      `<div class="card"><h2 style="color:var(--red)">Error</h2>` +
      `<pre style="color:var(--red);">${escapeHtml(err.message)}</pre></div>`;
    $("results-section").style.display = "block";
  } finally {
    btn.disabled = false;
  }
}

// ── BOM-only endpoint ────────────────────────────────────────────────────────

async function runBomOnly() {
  const description = $("prompt-input").value.trim() || $("prompt-input").placeholder;
  const provider = $("provider-select").value;

  try {
    const result = await fetchJSON(`${API}/bom`, {
      method: "POST",
      body: JSON.stringify({ description, provider }),
    });

    if (result.error) {
      throw new Error(result.error);
    }

    renderBom(result.bom);
    $("results-section").style.display = "block";
    setBadge("pipeline-badge", "BOM Ready", "success");
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  checkStatus();
  setInterval(checkStatus, 30000); // refresh status every 30s

  $("run-btn").addEventListener("click", runPipeline);
});
