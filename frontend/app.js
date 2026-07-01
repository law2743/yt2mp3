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

const stepTwoStages = {
  stems: ["人聲分離", "正在分離人聲與伴奏，供後續旋律分析使用。"],
  rmvpe: ["RMVPE", "正在使用 RMVPE 擷取人聲音高。"],
  torchcrepe: ["torchcrepe", "正在使用 torchcrepe 交叉比對音高。"],
  fcpe: ["FCPE", "正在使用 FCPE 補充音高候選。"],
  pesto: ["PESTO", "正在使用 PESTO 補充音高候選。"],
  fusion: ["多模型融合", "正在整合四個模型的音高結果。"],
  postprocess: ["旋律後處理", "正在清理跳音、短音與不穩定片段。"],
  rhythm: ["節奏分析", "正在分析 BPM、拍點與小節位置。"],
  notes: ["音符草稿", "正在把旋律整理成可量化的音符。"],
  notation: ["新版簡譜", "正在產生新版簡譜草稿。"],
  completed: ["新版簡譜", "新版簡譜草稿已完成。"],
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

function showProcessingStatus(message, createdAt = null, detail = null) {
  const elapsed = elapsedText(createdAt);
  statusBox.className = "status processing-status";
  statusBox.innerHTML = `
    <span class="processing-spinner" aria-hidden="true"></span>
    <span><strong>${escapeHtml(message)}</strong>
    <small>${escapeHtml(detail || elapsed || "後台正在持續處理，請保留此頁面")}</small></span>`;
}

function stepTwoStageFromArtifacts(melody = null) {
  const artifacts = melody?.artifact_status || {};
  if (!artifacts.vocals_wav) return "stems";
  if (!artifacts.rmvpe_csv) return "rmvpe";
  if (!artifacts.torchcrepe_csv) return "torchcrepe";
  if (!artifacts.fcpe_csv) return "fcpe";
  if (!artifacts.pesto_csv) return "pesto";
  if (!artifacts.fusion_csv || !artifacts.fusion_json) return "fusion";
  if (!artifacts.melody_json) return "postprocess";
  if (!artifacts.beat_grid_json || !artifacts.vocal_onsets_csv) return "rhythm";
  if (!artifacts.notes_draft_json) return "notes";
  if (!artifacts.numbered_notation_json || !artifacts.jianpu_draft_txt) return "notation";
  return "completed";
}

function showStepTwoStatus(melody = null) {
  const [title, detail] = stepTwoStages[stepTwoStageFromArtifacts(melody)] || stepTwoStages.rmvpe;
  showProcessingStatus(title, null, detail);
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

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
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

function fitNotationBlocks() {
  resultBox.querySelectorAll(".numbered-notation").forEach((block) => {
    block.style.setProperty("--notation-font-size", "1.1rem");
    const maxPx = 17.6;
    const minPx = 12;
    const available = block.clientWidth - 36;
    const needed = block.scrollWidth - 36;
    if (available <= 0 || needed <= available) return;
    const next = Math.max(minPx, Math.floor(maxPx * (available / needed) * 10) / 10);
    block.style.setProperty("--notation-font-size", `${next}px`);
  });
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
  const stemsAvailable = job.features?.stem_separation === true;
  const notationAvailable = job.notation_artifacts?.available === true;
  const stepTwoAvailable = stemsAvailable || notationAvailable;
  const alternatives = analysis.candidates.slice(1)
    .map((item) => `${escapeHtml(displayKeyName(item.key))} ${Math.round(item.score * 100)}%`).join("、") || "無";
  const buttons = [...job.shift_options]
    .sort((left, right) => left.semitones - right.semitones)
    .map((option) => {
      const targetKey = displayKeyName(option.target_key);
      return `
        <div class="shift-option">
          <span class="shift-label">${escapeHtml(option.label)}</span>
          <button class="shift-button${option.semitones === 0 ? " is-original" : ""}"
            type="button" data-shift="${option.semitones}"
            aria-label="${escapeHtml(`${option.label}，${targetKey}`)}">
            <span class="shift-button-label">${escapeHtml(option.label)}</span>
            <strong>${escapeHtml(targetKey)}</strong>
          </button>
        </div>`;
    }).join("");
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
      下載 ${escapeHtml(option.label)}・${escapeHtml(displayKeyName(option.target_key))}・${output.bitrate_kbps} kbps MP3
    </button>`;
  }).join("");
  const stepTwoPanel = stepTwoAvailable ? `
    <section class="stems-panel" data-phase-content="2" data-stems-panel aria-live="polite">
      <div class="panel-heading">
        <div>
          <h3>${stemsAvailable ? "人聲／伴奏分離" : "依拍點整理的簡譜草稿"}</h3>
          <p class="muted">${stemsAvailable
            ? "使用本機 GPU 產生練唱素材；失敗時不影響原本的分析與轉調。"
            : "已偵測到新版簡譜草稿；不影響原本的分析與轉調。"}</p>
        </div>
        ${stemsAvailable
          ? '<button class="primary-action" type="button" data-start-step2>產生人聲／伴奏</button>'
          : ""}
      </div>
      <div data-stems-content>
        ${stemsAvailable ? "" : notationDraftPanel(job.notation_artifacts)}
      </div>
    </section>` : "";
  resultBox.innerHTML = `
    <div class="song-head">
      ${source.thumbnail_url ? '<img data-thumbnail class="hidden" alt="影片縮圖">' : ""}
      <div><p class="eyebrow">分析結果</p><h2>${escapeHtml(source.title)}</h2>
      <p class="muted">${escapeHtml(source.uploader || "未知頻道")}・${formatDuration(source.duration_seconds)}</p></div>
    </div>
    <section data-phase-content="1">
    <div class="key-summary"><div><span>原調</span><strong>${escapeHtml(displayKeyName(analysis.key || analysis.display_name))}</strong></div>
      <div><span>可信度</span><strong>${Math.round(analysis.confidence * 100)}%</strong></div></div>
    <p>${confidenceText(analysis.confidence)}</p><p class="muted">其他可能：${alternatives}</p>
    <fieldset class="bitrate-picker">
      <legend>下載位元率</legend>
      <div class="bitrate-options">${bitrateOptions}</div>
    </fieldset>
    <div class="shift-grid" aria-label="轉調選項">${buttons}</div>
    <div class="downloads">${downloads}</div>
    </section>
    ${stepTwoPanel}`;
  resultBox.classList.remove("hidden");
  setPhaseTwoAvailable(stepTwoAvailable);
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
  if (stemsAvailable) await loadStemsState(job.job_id);
  else if (notationAvailable) {
    loadNotationDraft(job.notation_artifacts);
    bindMelodyActions(job.job_id);
  }
}

function displayKeyName(value) {
  const text = String(value || "").trim();
  const match = text.match(/^([A-G](?:#|b)?)(?:\s+|-)?(Major|Minor)$/i);
  if (!match) return text;
  return match[1] + (match[2].toLowerCase() === "minor" ? "m" : "");
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
  const panel = resultBox;
  panel?.querySelector("[data-start-melody]")?.addEventListener("click", () => {
    const meterHint = panel.querySelector("[data-melody-meter]")?.value || "auto";
    const source = panel.querySelector("[data-start-melody]").dataset.source || "auto";
    startMelody(jobId, meterHint, panel.querySelector("[data-start-melody]").dataset.force === "true", source);
  });
  panel?.querySelectorAll("[data-melody-download]").forEach((button) => {
    button.addEventListener("click", () => downloadArtifact(button.dataset.melodyDownload, button));
  });
  panel?.querySelectorAll("[data-notation-download]").forEach((button) => {
    button.addEventListener("click", () => downloadArtifact(button.dataset.notationDownload, button));
  });
  panel?.querySelectorAll("[data-stem-download]").forEach((button) => {
    button.addEventListener("click", () => downloadArtifact(
      `${button.dataset.stemDownload}?bitrate_kbps=${encodeURIComponent(selectedBitrate())}`,
      button,
    ));
  });
}

function notationDownloadButtons(artifacts) {
  const downloads = [
    ["下載簡譜草稿 TXT", artifacts?.jianpu_draft_txt_url],
  ].filter(([, url]) => Boolean(url));
  if (!downloads.length) return "";
  return `<div class="notation-downloads">
    ${downloads.map(([label, url]) => `<button type="button"
      data-notation-download="${escapeHtml(url)}">${escapeHtml(label)}</button>`).join("")}
  </div>`;
}

function notationDraftPanel(artifacts) {
  if (!artifacts?.available) return "";
  return `
    <section class="notation-draft" data-notation-draft>
      <div class="notation-draft-head">
        <div>
          <h4>新版簡譜草稿</h4>
          <p class="muted">此為自動產生的簡譜草稿，可能需要人工校正。</p>
        </div>
        ${notationDownloadButtons(artifacts)}
      </div>
      <pre class="jianpu-draft-pre" tabindex="0" aria-label="新版簡譜草稿"
        data-notation-draft-text>正在讀取新版簡譜草稿…</pre>
    </section>`;
}

async function loadNotationDraft(artifacts) {
  if (!artifacts?.available) return false;
  const target = resultBox.querySelector("[data-notation-draft-text]");
  if (!target) return false;
  if (artifacts.jianpu_draft_txt_url) {
    try {
      const response = await authenticatedFetch(artifacts.jianpu_draft_txt_url);
      const text = await response.text();
      target.textContent = text || "新版簡譜草稿目前沒有文字內容。";
      return Boolean(text);
    } catch (error) {
      console.debug("Failed to load jianpu draft txt", error);
    }
  }
  if (artifacts.numbered_notation_json_url) {
    try {
      const payload = await request(artifacts.numbered_notation_json_url);
      if (payload?.jianpu_text) {
        target.textContent = payload.jianpu_text;
        return true;
      }
    } catch (error) {
      console.debug("Failed to load numbered notation json", error);
    }
  }
  target.textContent = "新版簡譜草稿暫時無法讀取。";
  return false;
}

function legacyMelodyNotationBlock(result, hidden = false) {
  const lines = result.preview?.numbered_notation_lines || [];
  const notation = lines.length ? lines.map(escapeHtml).join("\n") : "沒有足夠清楚的旋律候選音符。";
  return `<pre class="numbered-notation${hidden ? " hidden" : ""}" tabindex="0"
    aria-label="主旋律簡譜草稿" data-legacy-notation>${notation}</pre>`;
}

function setTransposeControlsDisabled(disabled) {
  resultBox.querySelectorAll(".shift-button,.bitrate-option input").forEach((control) => {
    control.disabled = disabled;
  });
}

function renderMelodyState(jobId, melody) {
  const content = resultBox.querySelector("[data-melody-content]") || resultBox.querySelector("[data-stems-content]");
  if (!content) return;
  const running = [
    "melody_queued", "melody_preparing", "melody_extracting_pitch", "melody_exporting",
  ].includes(melody.status);
  setTransposeControlsDisabled(running);
  if (running) {
    content.innerHTML = "";
  } else if (melody.status === "melody_completed" && melody.result) {
    const result = melody.result;
    const summary = result.summary;
    const hasNotationArtifacts = melody.notation_artifacts?.available === true;
    const stems = resultBox.querySelector("[data-stems-content]");
    const vocalsUrl = stems?.dataset.vocalsUrl || "";
    const accompanimentUrl = stems?.dataset.accompanimentUrl || "";
    content.innerHTML = `
      <div class="stem-downloads">
        <button type="button" data-stem-download="${escapeHtml(vocalsUrl)}">下載人聲 MP3</button>
        <button type="button" data-stem-download="${escapeHtml(accompanimentUrl)}">下載伴奏 MP3</button>
        <button type="button" data-melody-download="${escapeHtml(result.downloads.midi_url)}">下載 MIDI</button>
      </div>
      <div class="melody-summary">
        <div><span>估計 BPM</span><strong>${result.bpm ? Math.round(result.bpm) : "—"}</strong></div>
        <div><span>拍號</span><strong>${escapeHtml(result.meter_used || "none")}</strong></div>
        <div><span>平均可信度</span><strong>${Math.round(summary.average_confidence * 100)}%</strong></div>
        <div><span>音域</span><strong>${escapeHtml(summary.estimated_range || "—")}</strong></div>
      </div>
      ${notationDraftPanel(melody.notation_artifacts)}
      ${legacyMelodyNotationBlock(result, hasNotationArtifacts)}`;
    fitNotationBlocks();
    if (hasNotationArtifacts) {
      loadNotationDraft(melody.notation_artifacts).then((loaded) => {
        const legacy = resultBox.querySelector("[data-legacy-notation]");
        const draft = resultBox.querySelector("[data-notation-draft]");
        if (!legacy) return;
        legacy.classList.toggle("hidden", loaded);
        if (draft) draft.classList.toggle("hidden", !loaded);
        if (!loaded) fitNotationBlocks();
      });
    }
  } else if (melody.status === "melody_failed") {
    content.innerHTML = `<p class="error">${escapeHtml(melody.error?.message || "無法產生主旋律草稿。")}</p>
      <p class="muted">可使用右上方按鈕重新嘗試。</p>`;
  } else {
    content.innerHTML = "";
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
    const content = resultBox.querySelector("[data-melody-content]") || resultBox.querySelector("[data-stems-content]");
    if (content) content.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
    setTransposeControlsDisabled(false);
  }
}

async function startMelody(jobId, meterHint, force = false, source = "auto") {
  clearMelodyPoll();
  setTransposeControlsDisabled(true);
  const content = resultBox.querySelector("[data-melody-content]") || resultBox.querySelector("[data-stems-content]");
  if (content) content.innerHTML = '<p class="muted">正在建立人聲 pitch 轉譜工作…</p>';
  try {
    const melody = await request(`/api/jobs/${encodeURIComponent(jobId)}/melody`, {
      method: "POST", body: JSON.stringify({ force, meter_hint: meterHint, source }),
    });
    renderMelodyState(jobId, melody);
    await loadMelodyState(jobId);
  } catch (error) {
    if (content) content.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
    bindMelodyActions(jobId);
    setTransposeControlsDisabled(false);
  }
}

function bindStemActions(jobId) {
  const panel = resultBox.querySelector("[data-stems-panel]");
  panel?.querySelector("[data-start-step2]")?.addEventListener("click", () => startStep2(jobId));
  panel?.querySelectorAll("[data-stem-download]").forEach((button) => {
    button.addEventListener("click", () => downloadArtifact(
      `${button.dataset.stemDownload}?bitrate_kbps=${encodeURIComponent(selectedBitrate())}`,
      button,
    ));
  });
}

function renderStemsState(jobId, stems) {
  const content = resultBox.querySelector("[data-stems-content]");
  if (!content) return;
  const running = ["stems_queued", "stems_running"].includes(stems.status);
  if (running) {
    content.innerHTML = "";
  } else if (stems.status === "stems_completed") {
    content.innerHTML = '<div data-melody-content></div>';
    content.dataset.vocalsUrl = stems.downloads.vocals_url || "";
    content.dataset.accompanimentUrl = stems.downloads.accompaniment_url || "";
  } else if (["stems_fallback", "stems_failed", "stems_skipped"].includes(stems.status)) {
    const warnings = stems.warnings || [];
    content.innerHTML = `<p class="melody-warning">目前無法使用正式人聲分離，已保留完整混音 CPU preview。</p>
      ${warnings.map((warning) => `<p class="melody-warning">${escapeHtml(warning)}</p>`).join("")}
      <p class="muted">可使用右上方按鈕重新嘗試。</p>`;
  } else {
    content.innerHTML = "";
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
    } else if (stems.status === "stems_completed") {
      await loadMelodyState(jobId);
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

async function waitForStems(jobId) {
  while (true) {
    const stems = await request(`/api/jobs/${encodeURIComponent(jobId)}/stems`);
    renderStemsState(jobId, stems);
    if (["stems_queued", "stems_running"].includes(stems.status)) {
      showStepTwoStatus();
      await delay(2000);
      continue;
    }
    if (stems.status === "stems_completed") return stems;
    throw new Error(stems.error?.message || "無法產生人聲／伴奏素材。");
  }
}

async function waitForMelody(jobId) {
  while (true) {
    const melody = await request(`/api/jobs/${encodeURIComponent(jobId)}/melody`);
    renderMelodyState(jobId, melody);
    if (["melody_queued", "melody_preparing", "melody_extracting_pitch", "melody_exporting"].includes(melody.status)) {
      showStepTwoStatus(melody);
      await delay(1500);
      continue;
    }
    if (melody.status === "melody_completed") return melody;
    throw new Error(melody.error?.message || "無法產生主旋律草稿。");
  }
}

async function startStep2(jobId) {
  clearStemsPoll();
  clearMelodyPoll();
  const button = resultBox.querySelector("[data-start-step2]");
  if (button) button.disabled = true;
  showStepTwoStatus();
  try {
    const currentStems = await request(`/api/jobs/${encodeURIComponent(jobId)}/stems`);
    if (["stems_queued", "stems_running"].includes(currentStems.status)) {
      await waitForStems(jobId);
    } else if (currentStems.status !== "stems_completed") {
      await request(`/api/jobs/${encodeURIComponent(jobId)}/stems`, {
        method: "POST", body: JSON.stringify({ force: false }),
      });
      await waitForStems(jobId);
    } else {
      renderStemsState(jobId, currentStems);
    }
    showStepTwoStatus({ artifact_status: { vocals_wav: true } });
    await request(`/api/jobs/${encodeURIComponent(jobId)}/melody`, {
      method: "POST", body: JSON.stringify({ force: true, meter_hint: "auto", source: "vocals" }),
    });
    await waitForMelody(jobId);
    showStatus("練唱素材已完成。", "success");
  } catch (error) {
    showStatus(error.message, "error");
  } finally {
    if (button) button.disabled = false;
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

window.addEventListener("resize", fitNotationBlocks);

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
