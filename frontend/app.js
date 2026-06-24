"use strict";

const TOKEN_KEY = "yt2mp3_access_token";
const TOKEN_EXPIRY_KEY = "yt2mp3_token_expires_at";
const JOB_KEY = "yt2mp3_job_id";
const apiBaseUrl = String(window.YT2MP3_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");
const form = document.querySelector("#analyze-form");
const urlInput = document.querySelector("#url");
const statusBox = document.querySelector("#status");
const resultBox = document.querySelector("#result");
const phaseStepper = document.querySelector("#phase-stepper");
let pollTimer = null;
let melodyPollTimer = null;
let stemsPollTimer = null;
let pollCount = 0;
let thumbnailObjectUrl = null;
let currentPhaseStep = 1;

function selectPhaseStep(step) {
  const target = phaseStepper.querySelector(`[data-phase-step="${step}"]`);
  if (!target || target.disabled) return;
  currentPhaseStep = step;
  phaseStepper.querySelectorAll("[data-phase-step]").forEach((button) => {
    const active = Number(button.dataset.phaseStep) === step;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  resultBox.querySelectorAll("[data-phase-content]").forEach((panel) => {
    panel.classList.toggle("hidden", Number(panel.dataset.phaseContent) !== step);
  });
}

function setPhaseTwoAvailable(available) {
  const button = phaseStepper.querySelector('[data-phase-step="2"]');
  button.disabled = !available;
  button.classList.toggle("is-available", available);
  if (!available && currentPhaseStep === 2) selectPhaseStep(1);
}

const stageLabels = {
  queued: "工作已排入佇列…",
  fetching_metadata: "正在讀取影片資訊…",
  downloading: "正在取得音訊…",
  preparing_audio: "正在準備分析音訊…",
  detecting_key: "正在分析歌曲調性…",
  awaiting_selection: "分析完成，請選擇升降半音。",
  queued_transpose: "轉調工作已排入佇列…",
  transposing: "正在轉調並製作 MP3…",
  completed: "MP3 已完成。",
};

function clearSession() {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_EXPIRY_KEY);
  sessionStorage.removeItem(JOB_KEY);
}

function token() {
  const value = sessionStorage.getItem(TOKEN_KEY);
  const expiry = Number(sessionStorage.getItem(TOKEN_EXPIRY_KEY));
  if (!value || !expiry || Date.now() >= expiry) {
    clearSession();
    window.location.replace("login.html");
    return null;
  }
  return value;
}

function showStatus(message, kind = "") {
  statusBox.className = `status ${kind}`.trim();
  statusBox.textContent = message;
}

function showStageProgress(message, percent) {
  const safePercent = Math.min(100, Math.max(0, Number(percent) || 0));
  statusBox.className = "status";
  statusBox.innerHTML = `
    <div class="progress-head"><span>${escapeHtml(message)}</span><strong>${safePercent}%</strong></div>
    <div class="progress-track" role="progressbar" aria-label="工作進度"
      aria-valuemin="0" aria-valuemax="100" aria-valuenow="${safePercent}">
      <span style="width:${safePercent}%"></span>
    </div>`;
}

function elapsedText(createdAt) {
  const startedAt = Date.parse(createdAt || "");
  if (!Number.isFinite(startedAt)) return "";
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  if (elapsedSeconds < 60) return `已處理 ${elapsedSeconds} 秒`;
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return `已處理 ${minutes} 分 ${seconds} 秒`;
}

function showProcessingStatus(message, createdAt = null) {
  const elapsed = elapsedText(createdAt);
  statusBox.className = "status processing-status";
  statusBox.innerHTML = `
    <span class="processing-spinner" aria-hidden="true"></span>
    <span><strong>${escapeHtml(message)}</strong>
    <small>${escapeHtml(elapsed || "後台正在持續處理，請保留此頁面")}</small></span>`;
}

async function authenticatedFetch(path, options = {}) {
  const accessToken = token();
  if (!accessToken) throw new Error("請先登入。");
  if (!/^https?:\/\//.test(apiBaseUrl)) throw new Error("前端尚未設定有效的後端網址。");
  let response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
  } catch (_error) {
    throw new Error("無法連線到本機後端，請確認 Tailscale 與服務是否已啟動。");
  }
  if (response.status === 401) {
    clearSession();
    window.location.replace("login.html");
    throw new Error("登入已過期，請重新登入。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message || "服務暫時無法完成操作。");
  }
  return response;
}

async function request(path, options = {}) {
  const response = await authenticatedFetch(path, options);
  return response.status === 204 ? null : response.json();
}

function rememberJob(jobId) {
  sessionStorage.setItem(JOB_KEY, jobId);
  const url = new URL(window.location.href);
  url.searchParams.set("job", jobId);
  history.replaceState(null, "", url);
}

function clearPoll() {
  if (pollTimer) window.clearTimeout(pollTimer);
  pollTimer = null;
}

function clearMelodyPoll() {
  if (melodyPollTimer) window.clearTimeout(melodyPollTimer);
  melodyPollTimer = null;
}

function clearStemsPoll() {
  if (stemsPollTimer) window.clearTimeout(stemsPollTimer);
  stemsPollTimer = null;
}

async function poll(jobId) {
  clearPoll();
  try {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    const stageLabel = stageLabels[job.stage] || "正在處理…";
    if (["downloading", "transposing"].includes(job.stage)) {
      showStageProgress(stageLabel, job.stage_progress);
    } else if (["queued", "fetching_metadata", "preparing_audio", "detecting_key", "queued_transpose"].includes(job.stage)) {
      showProcessingStatus(stageLabel, job.created_at);
    }
    else showStatus(stageLabel);
    if (job.status === "failed") {
      showStatus(job.error?.message || "處理失敗。", "error");
      return;
    }
    if (["ready", "completed"].includes(job.status)) await renderResult(job);
    if (!["ready", "completed", "failed", "cancelled", "expired"].includes(job.status)) {
      pollCount += 1;
      pollTimer = window.setTimeout(() => poll(jobId), pollCount < 10 ? 1000 : 2000);
    }
  } catch (error) {
    showStatus(error.message, "error");
    sessionStorage.removeItem(JOB_KEY);
  }
}

function confidenceText(value) {
  if (value >= 0.7) return "較高可信度";
  if (value >= 0.45) return "中等可信度，建議試聽確認";
  return "調性不明確，歌曲可能轉調或大／小調容易混淆";
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function estimatedMp3Size(seconds, bitrateKbps) {
  const bytes = (seconds * bitrateKbps * 1000 / 8) + (64 * 1024);
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function selectedBitrate() {
  return Number(resultBox.querySelector('input[name="bitrate"]:checked')?.value || 192);
}

async function loadThumbnail(path) {
  const image = resultBox.querySelector("[data-thumbnail]");
  if (!image || !path) return;
  try {
    const response = await authenticatedFetch(path);
    const blob = await response.blob();
    if (thumbnailObjectUrl) URL.revokeObjectURL(thumbnailObjectUrl);
    thumbnailObjectUrl = URL.createObjectURL(blob);
    image.src = thumbnailObjectUrl;
    image.classList.remove("hidden");
  } catch (_error) {
    image.remove();
  }
}

async function renderResult(job) {
  const source = job.source;
  const analysis = job.analysis;
  // Older backends do not include the additive features object. Once key
  // analysis is ready, expose Step 2 unless the API explicitly disables it.
  const melodyAvailable = job.features?.melody_analysis !== false;
  const stemsAvailable = job.features?.stem_separation === true;
  const alternatives = analysis.candidates.slice(1)
    .map((item) => `${escapeHtml(item.key)} ${Math.round(item.score * 100)}%`).join("、") || "無";
  const buttons = [...job.shift_options]
    .sort((left, right) => left.semitones - right.semitones)
    .map((option) => `
      <div class="shift-option">
        <span class="shift-label">${escapeHtml(option.label)}</span>
        <button class="shift-button${option.semitones === 0 ? " is-original" : ""}"
          type="button" data-shift="${option.semitones}"
          aria-label="${escapeHtml(`${option.label}，${option.target_key}`)}">
          <span class="shift-button-label">${escapeHtml(option.label)}</span>
          <strong>${escapeHtml(option.target_key)}</strong>
        </button>
      </div>`).join("");
  const bitrateOptions = [128, 192, 256].map((bitrate) => `
    <label class="bitrate-option">
      <input type="radio" name="bitrate" value="${bitrate}"${bitrate === 192 ? " checked" : ""}>
      <span><strong>${bitrate} kbps</strong>
      <small>預估 ${estimatedMp3Size(source.duration_seconds, bitrate)}</small></span>
    </label>`).join("");
  const downloads = job.outputs.map((output) => {
    const option = job.shift_options.find((item) => item.semitones === output.semitones);
    return `<button class="download" type="button" data-download="${output.semitones}"
      data-bitrate="${output.bitrate_kbps}">
      下載 ${escapeHtml(option.label)}・${escapeHtml(option.target_key)}・${output.bitrate_kbps} kbps MP3
    </button>`;
  }).join("");
  const stemsPanel = stemsAvailable ? `
    <section class="stems-panel" data-phase-content="2" data-stems-panel aria-live="polite">
      <h3>人聲／伴奏分離</h3>
      <p class="muted">使用本機 GPU 產生練唱素材；失敗時不影響原本的分析與轉調。</p>
      <div data-stems-content></div>
    </section>` : "";
  const melodyPanel = melodyAvailable ? `
    <section class="melody-panel" data-phase-content="2" data-melody-panel aria-live="polite">
      <h3>主旋律簡譜草稿</h3>
      <p class="muted">以 CPU-only pYIN 產生旋律預覽；有人聲 stem 時可改用人聲重新分析。</p>
      <div data-melody-content></div>
    </section>` : "";
  resultBox.innerHTML = `
    <div class="song-head">
      ${source.thumbnail_url ? '<img data-thumbnail class="hidden" alt="影片縮圖">' : ""}
      <div><p class="eyebrow">分析結果</p><h2>${escapeHtml(source.title)}</h2>
      <p class="muted">${escapeHtml(source.uploader || "未知頻道")}・${formatDuration(source.duration_seconds)}</p></div>
    </div>
    <section data-phase-content="1">
    <div class="key-summary"><div><span>原調</span><strong>${escapeHtml(analysis.key || analysis.display_name)}</strong></div>
      <div><span>可信度</span><strong>${Math.round(analysis.confidence * 100)}%</strong></div></div>
    <p>${confidenceText(analysis.confidence)}</p><p class="muted">其他可能：${alternatives}</p>
    <fieldset class="bitrate-picker">
      <legend>下載位元率</legend>
      <div class="bitrate-options">${bitrateOptions}</div>
    </fieldset>
    <div class="shift-grid" aria-label="轉調選項">${buttons}</div>
    <div class="downloads">${downloads}</div>
    </section>
    ${stemsPanel}
    ${melodyPanel}`;
  resultBox.classList.remove("hidden");
  setPhaseTwoAvailable(melodyAvailable || stemsAvailable);
  selectPhaseStep(currentPhaseStep);
  resultBox.querySelectorAll(".shift-button").forEach((button) => {
    button.addEventListener("click", () => startTranspose(
      job.job_id, Number(button.dataset.shift), selectedBitrate(),
    ));
  });
  resultBox.querySelectorAll("[data-download]").forEach((button) => {
    button.addEventListener("click", () => downloadMp3(
      job.job_id, Number(button.dataset.download), Number(button.dataset.bitrate), button,
    ));
  });
  await loadThumbnail(source.thumbnail_url);
  if (melodyAvailable) await loadMelodyState(job.job_id);
  if (stemsAvailable) await loadStemsState(job.job_id);
}

function melodyControls(meterHint = "auto", actionLabel = "產生主旋律簡譜草稿", force = false) {
  const meters = [
    ["auto", "自動（不確定時不分小節）"],
    ["none", "不分小節"],
    ["4/4", "4/4"],
    ["3/4", "3/4"],
    ["6/8", "6/8（3+3）"],
  ].map(([value, label]) => `<option value="${value}"${value === meterHint ? " selected" : ""}>${label}</option>`).join("");
  return `<div class="melody-controls">
    <label for="melody-meter">拍號提示</label>
    <select id="melody-meter" data-melody-meter>${meters}</select>
    <button type="button" data-start-melody data-force="${force}">${actionLabel}</button>
  </div>`;
}

function bindMelodyActions(jobId) {
  const panel = resultBox.querySelector("[data-melody-panel]");
  panel?.querySelector("[data-start-melody]")?.addEventListener("click", () => {
    const meterHint = panel.querySelector("[data-melody-meter]")?.value || "auto";
    const source = panel.querySelector("[data-start-melody]").dataset.source || "auto";
    startMelody(jobId, meterHint, panel.querySelector("[data-start-melody]").dataset.force === "true", source);
  });
  panel?.querySelectorAll("[data-melody-download]").forEach((button) => {
    button.addEventListener("click", () => downloadArtifact(button.dataset.melodyDownload, button));
  });
}

function setTransposeControlsDisabled(disabled) {
  resultBox.querySelectorAll(".shift-button,.bitrate-option input").forEach((control) => {
    control.disabled = disabled;
  });
}

function renderMelodyState(jobId, melody) {
  const content = resultBox.querySelector("[data-melody-content]");
  if (!content) return;
  const running = [
    "melody_queued", "melody_preparing", "melody_extracting_pitch", "melody_exporting",
  ].includes(melody.status);
  setTransposeControlsDisabled(running);
  if (running) {
    const labels = {
      melody_queued: "主旋律工作已排入佇列…",
      melody_preparing: "正在準備主旋律分析…",
      melody_extracting_pitch: "正在抽取主旋律候選音高…",
      melody_exporting: "正在輸出 Melody JSON 與 MIDI…",
    };
    content.innerHTML = `<div class="melody-progress">
      <span class="processing-spinner" aria-hidden="true"></span>
      <span><strong>${escapeHtml(labels[melody.status] || "正在分析主旋律…")}</strong>
      <small>${Math.round(melody.progress || 0)}%</small></span></div>`;
  } else if (melody.status === "melody_completed" && melody.result) {
    const result = melody.result;
    const lines = result.preview?.numbered_notation_lines || [];
    const notation = lines.length ? lines.map(escapeHtml).join("\n") : "沒有足夠清楚的旋律候選音符。";
    const summary = result.summary;
    const selectedSource = result.selected_source || result.melody_source_used || "mix";
    const requestedSource = result.requested_source || melody.source_requested || "auto";
    content.innerHTML = `
      <div class="melody-summary">
        <div><span>旋律來源</span><strong>${escapeHtml(selectedSource)}</strong></div>
        <div><span>來源選擇</span><strong>${escapeHtml(requestedSource)}</strong></div>
        <div><span>估計 BPM</span><strong>${result.bpm ? Math.round(result.bpm) : "—"}</strong></div>
        <div><span>拍號</span><strong>${escapeHtml(result.meter_used || "none")}</strong></div>
        <div><span>平均可信度</span><strong>${Math.round(summary.average_confidence * 100)}%</strong></div>
        <div><span>音域</span><strong>${escapeHtml(summary.estimated_range || "—")}</strong></div>
      </div>
      <pre class="numbered-notation" tabindex="0" aria-label="主旋律簡譜草稿">${notation}</pre>
      ${(result.warnings || []).map((warning) => `<p class="melody-warning">${escapeHtml(warning)}</p>`).join("")}
      <div class="melody-downloads">
        <button type="button" data-melody-download="${escapeHtml(result.downloads.json_url)}">下載 melody JSON</button>
        <button type="button" data-melody-download="${escapeHtml(result.downloads.midi_url)}">下載 MIDI</button>
      </div>
      ${melodyControls(melody.meter_hint, "重新分析", true)}`;
  } else if (melody.status === "melody_failed") {
    content.innerHTML = `<p class="error">${escapeHtml(melody.error?.message || "無法產生主旋律草稿。")}</p>
      ${melodyControls(melody.meter_hint, "重新嘗試", true)}`;
  } else {
    content.innerHTML = melodyControls(melody.meter_hint);
  }
  bindMelodyActions(jobId);
}

async function loadMelodyState(jobId) {
  try {
    const melody = await request(`/api/jobs/${encodeURIComponent(jobId)}/melody`);
    renderMelodyState(jobId, melody);
    if (["melody_queued", "melody_preparing", "melody_extracting_pitch", "melody_exporting"].includes(melody.status)) {
      clearMelodyPoll();
      melodyPollTimer = window.setTimeout(() => loadMelodyState(jobId), 1500);
    }
  } catch (error) {
    const content = resultBox.querySelector("[data-melody-content]");
    if (content) content.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
    setTransposeControlsDisabled(false);
  }
}

async function startMelody(jobId, meterHint, force = false, source = "auto") {
  clearMelodyPoll();
  setTransposeControlsDisabled(true);
  const content = resultBox.querySelector("[data-melody-content]");
  if (content) content.innerHTML = '<p class="muted">正在建立主旋律分析工作…</p>';
  try {
    const melody = await request(`/api/jobs/${encodeURIComponent(jobId)}/melody`, {
      method: "POST", body: JSON.stringify({ force, meter_hint: meterHint, source }),
    });
    renderMelodyState(jobId, melody);
    await loadMelodyState(jobId);
  } catch (error) {
    if (content) content.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>${melodyControls(meterHint, "重新嘗試", true)}`;
    bindMelodyActions(jobId);
    setTransposeControlsDisabled(false);
  }
}

function bindStemActions(jobId) {
  const panel = resultBox.querySelector("[data-stems-panel]");
  panel?.querySelector("[data-start-stems]")?.addEventListener("click", () => startStems(jobId));
  panel?.querySelectorAll("[data-stem-download]").forEach((button) => {
    button.addEventListener("click", () => downloadArtifact(button.dataset.stemDownload, button));
  });
  panel?.querySelector("[data-vocals-melody]")?.addEventListener("click", () => {
    const meterHint = resultBox.querySelector("[data-melody-meter]")?.value || "auto";
    startMelody(jobId, meterHint, true, "vocals");
  });
}

function renderStemsState(jobId, stems) {
  const content = resultBox.querySelector("[data-stems-content]");
  if (!content) return;
  const running = ["stems_queued", "stems_running"].includes(stems.status);
  if (running) {
    content.innerHTML = `<div class="melody-progress">
      <span class="processing-spinner" aria-hidden="true"></span>
      <span><strong>${stems.status === "stems_queued" ? "GPU 工作已排入佇列…" : "正在分離人聲與伴奏…"}</strong>
      <small>${Math.round(stems.progress || 0)}%</small></span></div>`;
  } else if (stems.status === "stems_completed") {
    content.innerHTML = `<p class="success-copy">已產生人聲與伴奏素材。</p>
      <p class="muted">Backend：${escapeHtml(stems.backend)}・${escapeHtml(stems.model || "—")}・${escapeHtml(stems.device || "—")}</p>
      ${(stems.warnings || []).map((warning) => `<p class="melody-warning">${escapeHtml(warning)}</p>`).join("")}
      <div class="stem-downloads">
        <button type="button" data-stem-download="${escapeHtml(stems.downloads.vocals_url)}">下載人聲 WAV</button>
        <button type="button" data-stem-download="${escapeHtml(stems.downloads.accompaniment_url)}">下載伴奏 WAV</button>
      </div>
      <button class="secondary stem-melody-action" type="button" data-vocals-melody>使用人聲重新產生簡譜草稿</button>`;
  } else if (["stems_fallback", "stems_failed", "stems_skipped"].includes(stems.status)) {
    const warnings = stems.warnings || [];
    content.innerHTML = `<p class="melody-warning">目前無法使用正式人聲分離，已保留完整混音 CPU preview。</p>
      ${warnings.map((warning) => `<p class="melody-warning">${escapeHtml(warning)}</p>`).join("")}
      <button type="button" data-start-stems>重新嘗試產生練唱素材</button>`;
  } else {
    content.innerHTML = '<button type="button" data-start-stems>產生人聲／伴奏</button>';
  }
  bindStemActions(jobId);
}

async function loadStemsState(jobId) {
  try {
    const stems = await request(`/api/jobs/${encodeURIComponent(jobId)}/stems`);
    renderStemsState(jobId, stems);
    if (["stems_queued", "stems_running"].includes(stems.status)) {
      clearStemsPoll();
      stemsPollTimer = window.setTimeout(() => loadStemsState(jobId), 2000);
    }
  } catch (error) {
    const content = resultBox.querySelector("[data-stems-content]");
    if (content) content.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

async function startStems(jobId) {
  clearStemsPoll();
  const content = resultBox.querySelector("[data-stems-content]");
  if (content) content.innerHTML = '<p class="muted">正在建立 GPU 分離工作…</p>';
  try {
    const stems = await request(`/api/jobs/${encodeURIComponent(jobId)}/stems`, {
      method: "POST", body: JSON.stringify({ force: false }),
    });
    renderStemsState(jobId, stems);
    await loadStemsState(jobId);
  } catch (error) {
    if (content) content.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

async function startTranspose(jobId, semitones, bitrateKbps) {
  resultBox.querySelectorAll("button,input").forEach((control) => { control.disabled = true; });
  showStatus("正在建立轉調工作…");
  try {
    const response = await request(`/api/jobs/${encodeURIComponent(jobId)}/transpose`, {
      method: "POST", body: JSON.stringify({ semitones, bitrate_kbps: bitrateKbps }),
    });
    pollCount = 0;
    await poll(jobId);
    if (response.cached) showStatus("已使用先前完成的 MP3。", "success");
  } catch (error) {
    showStatus(error.message, "error");
    resultBox.querySelectorAll("button,input").forEach((control) => { control.disabled = false; });
  }
}

function responseFilename(response) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) return decodeURIComponent(encoded);
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] || "yt2mp3.mp3";
}

async function downloadArtifact(path, button) {
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "正在下載…";
  try {
    const response = await authenticatedFetch(path);
    const objectUrl = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = responseFilename(response);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (error) {
    showStatus(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function downloadMp3(jobId, semitones, bitrateKbps, button) {
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "正在下載…";
  try {
    const response = await authenticatedFetch(
      `/api/jobs/${encodeURIComponent(jobId)}/download/${semitones}`
        + `?bitrate_kbps=${encodeURIComponent(bitrateKbps)}`,
    );
    const objectUrl = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = responseFilename(response);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (error) {
    showStatus(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearPoll();
  clearMelodyPoll();
  clearStemsPoll();
  currentPhaseStep = 1;
  setPhaseTwoAvailable(false);
  selectPhaseStep(1);
  resultBox.classList.add("hidden");
  showProcessingStatus("正在建立分析工作…");
  try {
    const response = await request("/api/jobs/analyze", {
      method: "POST", body: JSON.stringify({ url: urlInput.value.trim() }),
    });
    rememberJob(response.job_id);
    pollCount = 0;
    await poll(response.job_id);
  } catch (error) {
    showStatus(error.message, "error");
  }
});

document.querySelector("#logout").addEventListener("click", () => {
  clearSession();
  window.location.replace("login.html");
});

phaseStepper.querySelectorAll("[data-phase-step]").forEach((button) => {
  button.addEventListener("click", () => selectPhaseStep(Number(button.dataset.phaseStep)));
});

if (token()) {
  const savedJob = new URL(window.location.href).searchParams.get("job") || sessionStorage.getItem(JOB_KEY);
  if (savedJob) poll(savedJob);
}

window.addEventListener("beforeunload", () => {
  clearMelodyPoll();
  clearStemsPoll();
  if (thumbnailObjectUrl) URL.revokeObjectURL(thumbnailObjectUrl);
});
