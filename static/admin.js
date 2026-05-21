(() => {
  if (!window.__ADMIN_AUTHED__) return;

  let adminState = window.__ADMIN_STATE__ || {};
  const BASE_PATH = String(window.__BASE_PATH__ || "").replace(/\/$/, "");
  let selectedPlanId = adminState.active_plan_id || adminState.plans?.[0]?.id || "";
  let editingItemId = "";

  const $ = (id) => document.getElementById(id);

  const els = {
    queueInput: $("queueInput"),
    dpiInput: $("dpiInput"),
    activePaperSelect: $("activePaperSelect"),
    figmaFileKeyInput: $("figmaFileKeyInput"),
    figmaTokenEnvInput: $("figmaTokenEnvInput"),
    figmaScaleInput: $("figmaScaleInput"),
    settingsMessage: $("settingsMessage"),
    refreshFramesBtn: $("refreshFramesBtn"),
    saveSettingsBtn: $("saveSettingsBtn"),
    planList: $("planList"),
    editorTitle: $("editorTitle"),
    planNameInput: $("planNameInput"),
    planModeInput: $("planModeInput"),
    planStartInput: $("planStartInput"),
    planNotesInput: $("planNotesInput"),
    itemKindInput: $("itemKindInput"),
    itemNameInput: $("itemNameInput"),
    itemFrameInput: $("itemFrameInput"),
    itemPresetInput: $("itemPresetInput"),
    itemCopiesInput: $("itemCopiesInput"),
    itemThresholdInput: $("itemThresholdInput"),
    itemOverlayPositionInput: $("itemOverlayPositionInput"),
    itemOverlayTextInput: $("itemOverlayTextInput"),
    itemOffsetInput: $("itemOffsetInput"),
    itemRunAtInput: $("itemRunAtInput"),
    countdownOffsetInput: $("countdownOffsetInput"),
    countdownRunAtInput: $("countdownRunAtInput"),
    countdownStartMinutesInput: $("countdownStartMinutesInput"),
    countdownEndMinutesInput: $("countdownEndMinutesInput"),
    countdownIntervalInput: $("countdownIntervalInput"),
    countdownTemplateInput: $("countdownTemplateInput"),
    saveItemBtn: $("saveItemBtn"),
    clearItemBtn: $("clearItemBtn"),
    savePlanBtn: $("savePlanBtn"),
    planMessage: $("planMessage"),
    planItemsTable: $("planItemsTable"),
    recentJobs: $("recentJobs"),
    newPlanBtn: $("newPlanBtn"),
    activatePlanBtn: $("activatePlanBtn"),
    armPlanBtn: $("armPlanBtn"),
    startPlanBtn: $("startPlanBtn"),
    pausePlanBtn: $("pausePlanBtn"),
    resetPlanBtn: $("resetPlanBtn"),
    deletePlanBtn: $("deletePlanBtn"),
  };

  function showMessage(el, text, isError = false) {
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("error-text", Boolean(isError));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function paperPresets() {
    return adminState.printer?.paper_presets || [];
  }

  function frames() {
    return adminState.figma?.cached_frames || [];
  }

  function currentPlan() {
    return (adminState.plans || []).find((plan) => plan.id === selectedPlanId) || null;
  }

  function createBlankPlan() {
    return {
      id: `plan_${Math.random().toString(16).slice(2, 10)}`,
      name: "New Presentation Plan",
      notes: "",
      mode: "relative",
      start_at: "",
      started_at: "",
      stopped_at: "",
      completed_at: "",
      status: "draft",
      items: [],
    };
  }

  function createBlankItem(kind = "frame") {
    return {
      id: `item_${Math.random().toString(16).slice(2, 10)}`,
      kind,
      name: kind === "countdown" ? "Countdown stickers" : "Timed sticker",
      frame_id: "",
      frame_name: "",
      preset_key: paperPresets()[0]?.key || "",
      copies: 1,
      threshold: 160,
      overlay_text: "",
      overlay_position: "bottom",
      offset_seconds: 0,
      run_at: "",
      first_offset_seconds: 0,
      first_run_at: "",
      start_minutes: 4,
      end_minutes: 0,
      interval_seconds: 60,
      text_template: "{{minutes_left}} Mins left",
      printed_at: "",
      printed_ticks: [],
    };
  }

  function parseOffset(text) {
    const raw = String(text || "").trim();
    if (!raw) return 0;
    if (/^\d+$/.test(raw)) return Number(raw);
    const parts = raw.split(":").map((part) => part.trim());
    if (parts.some((part) => !/^\d+$/.test(part))) return 0;
    let seconds = 0;
    for (const part of parts) seconds = seconds * 60 + Number(part);
    return seconds;
  }

  function formatOffset(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }

  function toLocalInput(isoString) {
    if (!isoString) return "";
    return String(isoString).slice(0, 16);
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.msg || `Request failed: ${response.status}`);
    }
    return payload;
  }

  function setState(nextState) {
    adminState = nextState;
    if (!(adminState.plans || []).some((plan) => plan.id === selectedPlanId)) {
      selectedPlanId = adminState.active_plan_id || adminState.plans?.[0]?.id || "";
    }
    renderAll();
  }

  function renderSettings() {
    els.queueInput.value = adminState.printer?.queue || "";
    els.dpiInput.value = adminState.printer?.dpi || 300;
    els.figmaFileKeyInput.value = adminState.figma?.file_key || "";
    els.figmaTokenEnvInput.value = adminState.figma?.token_env || "FIGMA_TOKEN";
    els.figmaScaleInput.value = adminState.figma?.default_scale || 2;

    const presetOptions = paperPresets()
      .map((preset) => `<option value="${escapeHtml(preset.key)}">${escapeHtml(preset.label || preset.name)}</option>`)
      .join("");
    els.activePaperSelect.innerHTML = presetOptions;
    els.activePaperSelect.value = adminState.printer?.paper_presets?.find((preset) => preset.key === adminState.printer?.active_paper_key)?.key
      || adminState.printer?.paper_presets?.[0]?.key
      || paperPresets()[0]?.key
      || "";
  }

  function renderPlanList() {
    const plans = adminState.plans || [];
    if (!plans.length) {
      els.planList.innerHTML = `<p class="muted">No plans yet.</p>`;
      return;
    }
    els.planList.innerHTML = plans.map((plan) => `
      <button class="plan-card ${plan.id === selectedPlanId ? "active" : ""}" data-plan-id="${escapeHtml(plan.id)}" type="button">
        <span class="plan-card-title">${escapeHtml(plan.name)}</span>
        <span class="pill">${escapeHtml(plan.status || "draft")}</span>
        <span class="plan-card-sub">${escapeHtml(plan.mode === "absolute" ? "Absolute" : "Relative")}</span>
        <span class="plan-card-sub">${escapeHtml(plan.start_at || `${(plan.items || []).length} items`)}</span>
      </button>
    `).join("");

    els.planList.querySelectorAll("[data-plan-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedPlanId = button.dataset.planId || "";
        editingItemId = "";
        renderAll();
      });
    });
  }

  function renderFrameOptions(selectedFrameId = "") {
    const options = ['<option value="">Blank / overlay only</option>']
      .concat(frames().map((frame) => `
        <option value="${escapeHtml(frame.id)}">${escapeHtml(frame.path || frame.name)}</option>
      `));
    els.itemFrameInput.innerHTML = options.join("");
    els.itemFrameInput.value = selectedFrameId || "";
  }

  function renderPresetOptions(selectedKey = "") {
    els.itemPresetInput.innerHTML = paperPresets()
      .map((preset) => `<option value="${escapeHtml(preset.key)}">${escapeHtml(preset.label || preset.name)}</option>`)
      .join("");
    els.itemPresetInput.value = selectedKey || paperPresets()[0]?.key || "";
  }

  function syncTimingVisibility() {
    const plan = currentPlan() || createBlankPlan();
    const mode = els.planModeInput.value || plan.mode || "relative";
    const kind = els.itemKindInput.value || "frame";
    document.body.dataset.planMode = mode;
    document.body.dataset.itemKind = kind;
    document.querySelectorAll(".timing-frame").forEach((node) => {
      node.style.display = kind === "frame" ? "" : "none";
    });
    document.querySelectorAll(".timing-countdown").forEach((node) => {
      node.style.display = kind === "countdown" ? "" : "none";
    });
    document.querySelectorAll(".timing-frame-offset, .timing-countdown-offset").forEach((node) => {
      node.style.display = mode === "relative" ? "" : "none";
    });
    document.querySelectorAll(".timing-frame-absolute, .timing-countdown-absolute").forEach((node) => {
      node.style.display = mode === "absolute" ? "" : "none";
    });
  }

  function loadItemIntoForm(item) {
    const safeItem = item || createBlankItem();
    editingItemId = item?.id || "";
    els.itemKindInput.value = safeItem.kind || "frame";
    els.itemNameInput.value = safeItem.name || "";
    renderFrameOptions(safeItem.frame_id || "");
    renderPresetOptions(safeItem.preset_key || "");
    els.itemCopiesInput.value = safeItem.copies || 1;
    els.itemThresholdInput.value = safeItem.threshold || 160;
    els.itemOverlayPositionInput.value = safeItem.overlay_position || "bottom";
    els.itemOverlayTextInput.value = safeItem.overlay_text || "";
    els.itemOffsetInput.value = formatOffset(safeItem.offset_seconds || 0);
    els.itemRunAtInput.value = toLocalInput(safeItem.run_at || "");
    els.countdownOffsetInput.value = formatOffset(safeItem.first_offset_seconds || 0);
    els.countdownRunAtInput.value = toLocalInput(safeItem.first_run_at || "");
    els.countdownStartMinutesInput.value = safeItem.start_minutes ?? 4;
    els.countdownEndMinutesInput.value = safeItem.end_minutes ?? 0;
    els.countdownIntervalInput.value = safeItem.interval_seconds || 60;
    els.countdownTemplateInput.value = safeItem.text_template || "{{minutes_left}} Mins left";
    els.saveItemBtn.textContent = editingItemId ? "Update Item" : "Add Item";
    syncTimingVisibility();
  }

  function renderPlanForm() {
    const plan = clone(currentPlan() || createBlankPlan());
    els.editorTitle.textContent = plan.name || "Plan Editor";
    els.planNameInput.value = plan.name || "";
    els.planModeInput.value = plan.mode || "relative";
    els.planStartInput.value = toLocalInput(plan.start_at || "");
    els.planNotesInput.value = plan.notes || "";
    renderFrameOptions();
    renderPresetOptions();
    loadItemIntoForm(editingItemId ? plan.items.find((item) => item.id === editingItemId) : null);
    renderItemsTable(plan);
  }

  function renderItemsTable(plan) {
    if (!plan.items?.length) {
      els.planItemsTable.innerHTML = `<p class="muted">No sticker items in this plan yet.</p>`;
      return;
    }
    els.planItemsTable.innerHTML = `
      <div class="job-table">
        <div class="job-row job-head">
          <span>Item</span>
          <span>Timing</span>
          <span>Paper</span>
          <span>Frame</span>
          <span>Actions</span>
        </div>
        ${plan.items.map((item) => {
          const timing = plan.mode === "absolute"
            ? (item.kind === "countdown" ? (item.first_run_at || "") : (item.run_at || ""))
            : (item.kind === "countdown" ? formatOffset(item.first_offset_seconds || 0) : formatOffset(item.offset_seconds || 0));
          const frameText = item.kind === "countdown"
            ? `${escapeHtml(item.text_template || "")}`
            : escapeHtml(item.frame_name || "Blank");
          return `
            <div class="job-row">
              <span>${escapeHtml(item.name || "")}<br><small>${escapeHtml(item.kind)}</small></span>
              <span>${escapeHtml(timing)}</span>
              <span>${escapeHtml(item.preset_key || "")}</span>
              <span>${frameText}</span>
              <span class="button-row compact">
                <button class="ghost-btn" type="button" data-edit-item="${escapeHtml(item.id)}">Edit</button>
                <button class="ghost-btn" type="button" data-print-item="${escapeHtml(item.id)}">Print Now</button>
                <button class="ghost-btn danger" type="button" data-remove-item="${escapeHtml(item.id)}">Remove</button>
              </span>
            </div>
          `;
        }).join("")}
      </div>
    `;

    els.planItemsTable.querySelectorAll("[data-edit-item]").forEach((button) => {
      button.addEventListener("click", () => {
        const item = plan.items.find((entry) => entry.id === button.dataset.editItem);
        if (!item) return;
        loadItemIntoForm(item);
      });
    });

    els.planItemsTable.querySelectorAll("[data-remove-item]").forEach((button) => {
      button.addEventListener("click", () => {
        const planRef = currentPlan();
        if (!planRef) return;
        planRef.items = (planRef.items || []).filter((item) => item.id !== button.dataset.removeItem);
        editingItemId = "";
        renderAll();
      });
    });

    els.planItemsTable.querySelectorAll("[data-print-item]").forEach((button) => {
      button.addEventListener("click", async () => {
        const planRef = currentPlan();
        if (!planRef) return;
        try {
          const payload = await postJson(appUrl(`/api/admin/plans/${encodeURIComponent(planRef.id)}/print-now/${encodeURIComponent(button.dataset.printItem || "")}`));
          setState(payload.state);
          showMessage(els.planMessage, "Manual print sent.");
        } catch (error) {
          showMessage(els.planMessage, error.message, true);
        }
      });
    });
  }

  function renderRecentJobs() {
    const jobs = adminState.recent_jobs || [];
    if (!jobs.length) {
      els.recentJobs.innerHTML = `<p class="muted">No recent jobs.</p>`;
      return;
    }
    els.recentJobs.innerHTML = `
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

  function renderAll() {
    renderSettings();
    renderPlanList();
    renderPlanForm();
    renderRecentJobs();
    syncTimingVisibility();
  }

  function collectPlanMeta() {
    const plan = currentPlan() || createBlankPlan();
    plan.name = els.planNameInput.value.trim() || "Untitled Plan";
    plan.mode = els.planModeInput.value;
    plan.start_at = els.planStartInput.value || "";
    plan.notes = els.planNotesInput.value.trim();
    return plan;
  }

  function collectItemFromForm() {
    const kind = els.itemKindInput.value;
    const selectedFrame = frames().find((frame) => frame.id === els.itemFrameInput.value);
    const base = editingItemId
      ? clone((currentPlan()?.items || []).find((item) => item.id === editingItemId) || createBlankItem(kind))
      : createBlankItem(kind);
    base.kind = kind;
    base.name = els.itemNameInput.value.trim() || base.name;
    base.frame_id = els.itemFrameInput.value || "";
    base.frame_name = selectedFrame?.name || "";
    base.preset_key = els.itemPresetInput.value || paperPresets()[0]?.key || "";
    base.copies = Math.max(1, Number(els.itemCopiesInput.value || 1));
    base.threshold = Math.max(1, Math.min(255, Number(els.itemThresholdInput.value || 160)));
    base.overlay_position = els.itemOverlayPositionInput.value || "bottom";
    base.overlay_text = els.itemOverlayTextInput.value.trim();
    if (kind === "frame") {
      base.offset_seconds = parseOffset(els.itemOffsetInput.value);
      base.run_at = els.itemRunAtInput.value || "";
    } else {
      base.first_offset_seconds = parseOffset(els.countdownOffsetInput.value);
      base.first_run_at = els.countdownRunAtInput.value || "";
      base.start_minutes = Math.max(0, Number(els.countdownStartMinutesInput.value || 0));
      base.end_minutes = Math.max(0, Number(els.countdownEndMinutesInput.value || 0));
      base.interval_seconds = Math.max(1, Number(els.countdownIntervalInput.value || 60));
      base.text_template = els.countdownTemplateInput.value.trim() || "{{minutes_left}} Mins left";
    }
    return base;
  }

  function applyItemToPlan() {
    const plan = collectPlanMeta();
    const item = collectItemFromForm();
    const existingIndex = (plan.items || []).findIndex((entry) => entry.id === item.id);
    if (existingIndex >= 0) {
      plan.items[existingIndex] = item;
    } else {
      plan.items = [...(plan.items || []), item];
    }
    const plans = adminState.plans || [];
    const planIndex = plans.findIndex((entry) => entry.id === plan.id);
    if (planIndex >= 0) {
      plans[planIndex] = plan;
    } else {
      plans.push(plan);
      selectedPlanId = plan.id;
    }
    editingItemId = "";
    renderAll();
  }

  async function saveCurrentPlan() {
    const plan = collectPlanMeta();
    try {
      const payload = await postJson(appUrl("/api/admin/plans/save"), { plan });
      setState(payload.state);
      showMessage(els.planMessage, "Plan saved.");
    } catch (error) {
      showMessage(els.planMessage, error.message, true);
    }
  }

  async function runPlanAction(action) {
    const plan = currentPlan();
    if (!plan) return;
    try {
      const payload = await postJson(appUrl(`/api/admin/plans/${encodeURIComponent(plan.id)}/${encodeURIComponent(action)}`));
      setState(payload.state);
      showMessage(els.planMessage, `Plan ${action} complete.`);
    } catch (error) {
      showMessage(els.planMessage, error.message, true);
    }
  }

  els.saveSettingsBtn.addEventListener("click", async () => {
    try {
      const payload = await postJson(appUrl("/api/admin/settings"), {
        queue: els.queueInput.value.trim(),
        dpi: Number(els.dpiInput.value || 300),
        active_paper_key: els.activePaperSelect.value,
        file_key: els.figmaFileKeyInput.value.trim(),
        token_env: els.figmaTokenEnvInput.value.trim(),
        default_scale: Number(els.figmaScaleInput.value || 2),
      });
      setState(payload.state);
      showMessage(els.settingsMessage, "Settings saved.");
    } catch (error) {
      showMessage(els.settingsMessage, error.message, true);
    }
  });

  els.refreshFramesBtn.addEventListener("click", async () => {
    try {
      const payload = await postJson(appUrl("/api/admin/figma/refresh"));
      setState(payload.state);
      showMessage(els.settingsMessage, `Loaded ${payload.frames?.length || 0} Figma frames.`);
    } catch (error) {
      showMessage(els.settingsMessage, error.message, true);
    }
  });

  els.newPlanBtn.addEventListener("click", () => {
    const plan = createBlankPlan();
    adminState.plans = [...(adminState.plans || []), plan];
    selectedPlanId = plan.id;
    editingItemId = "";
    renderAll();
    showMessage(els.planMessage, "Draft plan created locally. Save it when ready.");
  });

  els.planModeInput.addEventListener("change", syncTimingVisibility);
  els.itemKindInput.addEventListener("change", syncTimingVisibility);

  els.clearItemBtn.addEventListener("click", () => {
    editingItemId = "";
    loadItemIntoForm(createBlankItem(els.itemKindInput.value || "frame"));
  });

  els.saveItemBtn.addEventListener("click", () => {
    applyItemToPlan();
    showMessage(els.planMessage, "Item staged in plan. Save the plan to persist it.");
  });

  els.savePlanBtn.addEventListener("click", saveCurrentPlan);
  els.activatePlanBtn.addEventListener("click", () => runPlanAction("activate"));
  els.armPlanBtn.addEventListener("click", () => runPlanAction("arm"));
  els.startPlanBtn.addEventListener("click", () => runPlanAction("start"));
  els.pausePlanBtn.addEventListener("click", () => runPlanAction("pause"));
  els.resetPlanBtn.addEventListener("click", () => runPlanAction("reset"));
  els.deletePlanBtn.addEventListener("click", async () => {
    const plan = currentPlan();
    if (!plan) return;
    if (!window.confirm(`Delete "${plan.name}"?`)) return;
    try {
      const payload = await postJson(appUrl(`/api/admin/plans/${encodeURIComponent(plan.id)}/delete`));
      setState(payload.state);
      showMessage(els.planMessage, "Plan deleted.");
    } catch (error) {
      showMessage(els.planMessage, error.message, true);
    }
  });

  renderAll();
})();
  function appUrl(path) {
    const normalized = `/${String(path || "").replace(/^\/+/, "")}`;
    return `${BASE_PATH}${normalized}`;
  }
