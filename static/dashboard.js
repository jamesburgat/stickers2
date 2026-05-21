(() => {
  const state = window.__DASHBOARD_STATE__ || {};
  const BASE_PATH = String(window.__BASE_PATH__ || "").replace(/\/$/, "");
  const activePlanEl = document.getElementById("activePlanContent");
  const paperEl = document.getElementById("paperPresetContent");
  const figmaEl = document.getElementById("figmaContent");
  const recentEl = document.getElementById("recentPrintsContent");

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderPlan(plan) {
    if (!plan) {
      activePlanEl.innerHTML = `<p class="muted">No active plan selected.</p>`;
      return;
    }
    const nextJobs = Array.isArray(plan.next_jobs) ? plan.next_jobs : [];
    activePlanEl.innerHTML = `
      <div class="summary-line">
        <strong>${escapeHtml(plan.name)}</strong>
        <span class="pill">${escapeHtml(plan.status || "draft")}</span>
      </div>
      <p class="muted">${escapeHtml(plan.mode === "absolute" ? "Absolute schedule" : "Relative schedule")}</p>
      ${plan.start_at_label ? `<p class="muted">Scheduled start: ${escapeHtml(plan.start_at_label)}</p>` : ""}
      ${plan.started_at_label ? `<p class="muted">Started: ${escapeHtml(plan.started_at_label)}</p>` : ""}
      <div class="mini-list">
        ${nextJobs.length ? nextJobs.map((job) => `
          <div class="mini-row">
            <span>${escapeHtml(job.name)}</span>
            <span>${escapeHtml(job.due_label || "")}</span>
          </div>
        `).join("") : `<p class="muted">No pending print events.</p>`}
      </div>
    `;
  }

  function renderPresets(presets) {
    const rows = (presets || []).map((preset) => `
      <div class="preset-chip">
        <span class="swatch" style="background:${escapeHtml(preset.theme_color || "#ddd")}"></span>
        <div>
          <strong>${escapeHtml(preset.name)}</strong>
          <div class="muted">${escapeHtml(preset.label || "")}</div>
        </div>
      </div>
    `);
    paperEl.innerHTML = rows.join("") || `<p class="muted">No paper presets configured.</p>`;
  }

  function renderFigma(figma) {
    figmaEl.innerHTML = `
      <p><strong>File Key:</strong> ${escapeHtml(figma.file_key || "Not set")}</p>
      <p><strong>Frames Cached:</strong> ${escapeHtml(figma.frame_count || 0)}</p>
      <p><strong>Last Sync:</strong> ${escapeHtml(figma.last_sync_at || "Never")}</p>
      ${figma.last_error ? `<p class="error-text">${escapeHtml(figma.last_error)}</p>` : ""}
    `;
  }

  function renderRecent(jobs) {
    if (!jobs || !jobs.length) {
      recentEl.innerHTML = `<p class="muted">No prints yet.</p>`;
      return;
    }
    recentEl.innerHTML = `
      <div class="job-table">
        <div class="job-row job-head">
          <span>Time</span>
          <span>Plan</span>
          <span>Sticker</span>
          <span>Paper</span>
        </div>
        ${jobs.map((job) => `
          <div class="job-row">
            <span>${escapeHtml(job.timestamp || "")}</span>
            <span>${escapeHtml(job.plan_name || "")}</span>
            <span>${escapeHtml(job.overlay_text || job.item_name || "")}</span>
            <span>${escapeHtml(job.preset_label || "")}</span>
          </div>
        `).join("")}
      </div>
    `;
  }

  function render(nextState) {
    renderPlan(nextState.active_plan || null);
    renderPresets(nextState.paper_presets || []);
    renderFigma(nextState.figma || {});
    renderRecent(nextState.recent_jobs || []);
  }

  async function poll() {
    try {
      const response = await fetch(appUrl("/api/state"), { headers: { "Accept": "application/json" } });
      if (!response.ok) return;
      const payload = await response.json();
      if (payload?.ok && payload.state) render(payload.state);
    } catch (_) {
      // Ignore transient polling failures on the display view.
    }
  }

  render(state);
  window.setInterval(poll, 5000);
})();
  function appUrl(path) {
    const normalized = `/${String(path || "").replace(/^\/+/, "")}`;
    return `${BASE_PATH}${normalized}`;
  }
