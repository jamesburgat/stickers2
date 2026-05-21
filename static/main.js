(() => {
  const state = window.__APP_STATE__ || {};
  const BASE_PATH = String(window.__BASE_PATH__ || "").replace(/\/$/, "");
  const DEFAULT_THRESHOLD = Number(window.__DEFAULT_THRESHOLD__ || 160);

  const $ = (id) => document.getElementById(id);
  const modal = $("modal");
  const cropImage = $("cropImage");
  const previewCanvas = $("previewCanvas");
  const thresholdSlider = $("threshold");
  const thresholdValue = $("thVal");
  const presetSelect = $("presetSelect");
  const textInput = $("textInput");
  const imageInput = $("imageInput");
  const textStatus = $("textStatus");
  const imageStatus = $("imageStatus");
  const timerStatus = $("timerStatus");
  const timerSummary = $("timerSummary");
  const historyGrid = $("historyGrid");
  const startAtInput = $("startAtInput");
  const endAtInput = $("endAtInput");
  const timerTemplateInput = $("timerTemplateInput");

  let cropper = null;
  let historyState = state.recent_jobs || [];
  let selectedHistoryIds = new Set();
  let timerState = state.countdown_timer || {};

  function appUrl(path) {
    return `${BASE_PATH}/${String(path || "").replace(/^\/+/, "")}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function say(node, text, isError = false) {
    if (!node) return;
    node.textContent = text || "";
    node.classList.toggle("error-text", Boolean(isError));
  }

  function activePreset() {
    const presets = state.paper_presets || [];
    return presets.find((preset) => preset.key === presetSelect.value) || presets[0] || null;
  }

  function paintWhite(canvas) {
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return ctx;
  }

  function initPreviewCanvas() {
    const preset = activePreset();
    if (!preset || !previewCanvas) return;
    previewCanvas.width = preset.px_w;
    previewCanvas.height = preset.px_h;
    const maxDisplay = window.matchMedia("(max-width: 720px)").matches ? 180 : 240;
    const scale = Math.min(1, maxDisplay / Math.max(preset.px_w, preset.px_h));
    previewCanvas.style.width = `${Math.round(preset.px_w * scale)}px`;
    previewCanvas.style.height = `${Math.round(preset.px_h * scale)}px`;
    paintWhite(previewCanvas);
  }

  function fitToLabelCanvas(sourceCanvas) {
    const preset = activePreset();
    if (!sourceCanvas || !preset) return null;
    const canvas = document.createElement("canvas");
    canvas.width = preset.px_w;
    canvas.height = preset.px_h;
    const ctx = paintWhite(canvas);
    const scale = Math.max(preset.px_w / sourceCanvas.width, preset.px_h / sourceCanvas.height);
    const drawW = Math.max(1, Math.round(sourceCanvas.width * scale));
    const drawH = Math.max(1, Math.round(sourceCanvas.height * scale));
    const dx = Math.floor((preset.px_w - drawW) / 2);
    const dy = Math.floor((preset.px_h - drawH) / 2);
    ctx.drawImage(sourceCanvas, dx, dy, drawW, drawH);
    return canvas;
  }

  function renderPreview() {
    const preset = activePreset();
    if (!cropper || !previewCanvas || !preset) return;
    let cropped;
    try {
      const raw = cropper.getCroppedCanvas({
        width: preset.px_w,
        height: preset.px_h,
        fillColor: "#fff",
        imageSmoothingEnabled: true,
        imageSmoothingQuality: "high",
      });
      cropped = fitToLabelCanvas(raw);
    } catch {
      return;
    }
    if (!cropped) return;
    const ctx = paintWhite(previewCanvas);
    const imgData = ctx.getImageData(0, 0, previewCanvas.width, previewCanvas.height);
    ctx.drawImage(cropped, 0, 0);
    const freshData = ctx.getImageData(0, 0, previewCanvas.width, previewCanvas.height);
    const threshold = parseInt(thresholdSlider.value || String(DEFAULT_THRESHOLD), 10);
    const data = freshData.data;
    for (let i = 0; i < data.length; i += 4) {
      const lum = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
      const out = lum > threshold ? 255 : 0;
      data[i] = data[i + 1] = data[i + 2] = out;
      data[i + 3] = 255;
    }
    ctx.putImageData(freshData, 0, 0);
  }

  function openModal() {
    initPreviewCanvas();
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeModal() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    if (cropper) {
      cropper.destroy();
      cropper = null;
    }
    if (cropImage) cropImage.removeAttribute("src");
  }

  function loadImageIntoCropper(file) {
    if (!file || !file.type?.startsWith("image/")) {
      say(imageStatus, "Choose an image file.", true);
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => say(imageStatus, "Could not read image.", true);
    reader.onload = () => {
      cropImage.onload = () => {
        const preset = activePreset();
        cropper = new Cropper(cropImage, {
          viewMode: 0,
          background: false,
          autoCropArea: 0.95,
          aspectRatio: preset ? preset.px_w / preset.px_h : 1,
          movable: true,
          zoomable: true,
          scalable: true,
          responsive: true,
          ready() { setTimeout(renderPreview, 0); },
          crop() { renderPreview(); },
        });
        openModal();
      };
      cropImage.onerror = () => say(imageStatus, "Image failed to decode.", true);
      cropImage.src = reader.result;
    };
    reader.readAsDataURL(file);
  }

  async function postJson(path, body) {
    const response = await fetch(appUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.msg || `Request failed: ${response.status}`);
    }
    return payload;
  }

  function renderHistory() {
    if (!historyGrid) return;
    if (!historyState.length) {
      historyGrid.innerHTML = `<p class="muted">No sticker history yet. Print a text or image sticker first.</p>`;
      return;
    }
    historyGrid.innerHTML = historyState.map((item) => `
      <label class="history-item">
        <input class="history-check" type="checkbox" value="${escapeHtml(item.history_id || "")}" ${selectedHistoryIds.has(item.history_id) ? "checked" : ""}>
        <div class="history-thumb">
          <img src="${escapeHtml(item.image_url || "")}" alt="${escapeHtml(item.label || "Sticker")}">
        </div>
        <div class="history-meta">
          <strong>${escapeHtml(item.label || "")}</strong>
          <span>${escapeHtml(item.timestamp || "")}</span>
          <span>${escapeHtml(item.preset_label || "")}</span>
        </div>
      </label>
    `).join("");
    historyGrid.querySelectorAll(".history-check").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selectedHistoryIds.add(checkbox.value);
        else selectedHistoryIds.delete(checkbox.value);
      });
    });
  }

  function renderTimerSummary() {
    const status = timerState.status || "idle";
    if (status === "idle") {
      timerSummary.innerHTML = `<span class="muted">No timer running.</span>`;
      return;
    }
    timerSummary.innerHTML = `
      <div class="timer-summary-block">
        <span class="pill">${escapeHtml(status)}</span>
        <span>${escapeHtml(timerState.start_at_label || "")} → ${escapeHtml(timerState.end_at_label || "")}</span>
        <span>${escapeHtml(timerState.minute_count || 0)} minute stickers</span>
      </div>
    `;
  }

  function applyState(nextState) {
    historyState = nextState.recent_jobs || [];
    timerState = nextState.countdown_timer || {};
    if (timerState.start_at) startAtInput.value = String(timerState.start_at).slice(0, 16);
    if (timerState.end_at) endAtInput.value = String(timerState.end_at).slice(0, 16);
    renderHistory();
    renderTimerSummary();
  }

  async function refreshState() {
    try {
      const response = await fetch(appUrl("/api/state"), { headers: { Accept: "application/json" } });
      const payload = await response.json();
      if (payload?.ok && payload.state) applyState(payload.state);
    } catch (_) {
      // ignore polling failures
    }
  }

  $("printTextBtn")?.addEventListener("click", async () => {
    const preset = activePreset();
    const text = (textInput.value || "").trim();
    if (!text) return say(textStatus, "Enter text first.", true);
    try {
      const payload = await postJson("/print-text", { text, preset_key: preset?.key || "" });
      textInput.value = "";
      say(textStatus, payload.msg || "Printed.");
      refreshState();
    } catch (error) {
      say(textStatus, error.message, true);
    }
  });

  $("cropBtn")?.addEventListener("click", () => {
    const file = imageInput.files?.[0];
    if (!file) {
      imageInput.click();
      return;
    }
    loadImageIntoCropper(file);
  });

  imageInput?.addEventListener("change", () => {
    const file = imageInput.files?.[0];
    if (file) loadImageIntoCropper(file);
  });

  $("closeModal")?.addEventListener("click", closeModal);
  $("zoomIn")?.addEventListener("click", () => { if (cropper) { cropper.zoom(0.1); renderPreview(); } });
  $("zoomOut")?.addEventListener("click", () => { if (cropper) { cropper.zoom(-0.1); renderPreview(); } });
  $("resetCrop")?.addEventListener("click", () => { if (cropper) { cropper.reset(); renderPreview(); } });
  $("rotateImage")?.addEventListener("click", () => { if (cropper) { cropper.rotate(90); renderPreview(); } });

  if (thresholdSlider && thresholdValue) {
    thresholdValue.textContent = String(DEFAULT_THRESHOLD);
    thresholdSlider.value = String(DEFAULT_THRESHOLD);
    thresholdSlider.addEventListener("input", () => {
      thresholdValue.textContent = thresholdSlider.value;
      renderPreview();
    });
  }

  $("printImageBtn")?.addEventListener("click", async () => {
    const preset = activePreset();
    if (!cropper || !preset) return say(imageStatus, "Choose an image first.", true);
    let cropped;
    try {
      const raw = cropper.getCroppedCanvas({
        width: preset.px_w,
        height: preset.px_h,
        fillColor: "#fff",
        imageSmoothingEnabled: true,
        imageSmoothingQuality: "high",
      });
      cropped = fitToLabelCanvas(raw);
    } catch {
      return say(imageStatus, "Image is not ready yet.", true);
    }
    const blob = await new Promise((resolve) => {
      try { cropped.toBlob((result) => resolve(result), "image/png", 1.0); }
      catch { resolve(null); }
    });
    if (!blob) return say(imageStatus, "Could not prepare image.", true);
    try {
      const form = new FormData();
      form.append("image", blob, "crop.png");
      form.append("threshold", thresholdSlider.value || String(DEFAULT_THRESHOLD));
      form.append("preset_key", preset.key);
      const response = await fetch(appUrl("/print-image"), { method: "POST", body: form });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.ok) throw new Error(payload?.msg || `Request failed: ${response.status}`);
      closeModal();
      imageInput.value = "";
      say(imageStatus, payload.msg || "Printed.");
      refreshState();
    } catch (error) {
      say(imageStatus, error.message, true);
    }
  });

  $("startTimerBtn")?.addEventListener("click", async () => {
    const preset = activePreset();
    try {
      const payload = await postJson("/api/timer/start", {
        start_at: startAtInput.value,
        end_at: endAtInput.value,
        preset_key: preset?.key || "",
        text_template: timerTemplateInput.value || "{{minutes_left}} Mins left",
        selected_history_ids: Array.from(selectedHistoryIds),
      });
      say(timerStatus, "Timer armed.");
      applyState(payload.state);
    } catch (error) {
      say(timerStatus, error.message, true);
    }
  });

  $("cancelTimerBtn")?.addEventListener("click", async () => {
    try {
      const payload = await postJson("/api/timer/cancel");
      say(timerStatus, "Timer cancelled.");
      applyState(payload.state);
    } catch (error) {
      say(timerStatus, error.message, true);
    }
  });

  window.addEventListener("resize", () => {
    if (modal.classList.contains("open")) {
      initPreviewCanvas();
      renderPreview();
    }
  });

  applyState(state);
  window.setInterval(refreshState, 10000);
})();
