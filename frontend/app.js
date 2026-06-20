"use strict";

const TOKEN_KEY = "yt2mp3_access_token";
const TOKEN_EXPIRY_KEY = "yt2mp3_token_expires_at";
const JOB_KEY = "yt2mp3_job_id";
const apiBaseUrl = String(window.YT2MP3_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");
const form = document.querySelector("#analyze-form");
const urlInput = document.querySelector("#url");
const statusBox = document.querySelector("#status");
const resultBox = document.querySelector("#result");
let pollTimer = null;
let pollCount = 0;
let thumbnailObjectUrl = null;

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

async function poll(jobId) {
  clearPoll();
  try {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    showStatus(stageLabels[job.stage] || "正在處理…");
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
  const alternatives = analysis.candidates.slice(1)
    .map((item) => `${escapeHtml(item.key)} ${Math.round(item.score * 100)}%`).join("、") || "無";
  const buttons = job.shift_options.map((option) => `
    <button class="shift-button" type="button" data-shift="${option.semitones}">
      <span>${escapeHtml(option.label)}</span><strong>${escapeHtml(option.target_key)}</strong>
    </button>`).join("");
  const downloads = job.outputs.map((shift) => {
    const option = job.shift_options.find((item) => item.semitones === shift);
    return `<button class="download" type="button" data-download="${shift}">
      下載 ${escapeHtml(option.label)}・${escapeHtml(option.target_key)} MP3
    </button>`;
  }).join("");
  resultBox.innerHTML = `
    <div class="song-head">
      ${source.thumbnail_url ? '<img data-thumbnail class="hidden" alt="影片縮圖">' : ""}
      <div><p class="eyebrow">分析結果</p><h2>${escapeHtml(source.title)}</h2>
      <p class="muted">${escapeHtml(source.uploader || "未知頻道")}・${formatDuration(source.duration_seconds)}</p></div>
    </div>
    <div class="key-summary"><div><span>原調</span><strong>${escapeHtml(analysis.key || analysis.display_name)}</strong></div>
      <div><span>可信度</span><strong>${Math.round(analysis.confidence * 100)}%</strong></div></div>
    <p>${confidenceText(analysis.confidence)}</p><p class="muted">其他可能：${alternatives}</p>
    <div class="shift-grid" aria-label="轉調選項">${buttons}</div>
    <div class="downloads">${downloads}</div>`;
  resultBox.classList.remove("hidden");
  resultBox.querySelectorAll(".shift-button").forEach((button) => {
    button.addEventListener("click", () => startTranspose(job.job_id, Number(button.dataset.shift)));
  });
  resultBox.querySelectorAll("[data-download]").forEach((button) => {
    button.addEventListener("click", () => downloadMp3(job.job_id, Number(button.dataset.download), button));
  });
  await loadThumbnail(source.thumbnail_url);
}

async function startTranspose(jobId, semitones) {
  resultBox.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  showStatus("正在建立轉調工作…");
  try {
    const response = await request(`/api/jobs/${encodeURIComponent(jobId)}/transpose`, {
      method: "POST", body: JSON.stringify({ semitones }),
    });
    pollCount = 0;
    await poll(jobId);
    if (response.cached) showStatus("已使用先前完成的 MP3。", "success");
  } catch (error) {
    showStatus(error.message, "error");
    resultBox.querySelectorAll("button").forEach((button) => { button.disabled = false; });
  }
}

function responseFilename(response) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) return decodeURIComponent(encoded);
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] || "yt2mp3.mp3";
}

async function downloadMp3(jobId, semitones, button) {
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "正在下載…";
  try {
    const response = await authenticatedFetch(
      `/api/jobs/${encodeURIComponent(jobId)}/download/${semitones}`,
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
  resultBox.classList.add("hidden");
  showStatus("正在建立分析工作…");
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

if (token()) {
  const savedJob = new URL(window.location.href).searchParams.get("job") || sessionStorage.getItem(JOB_KEY);
  if (savedJob) poll(savedJob);
}

window.addEventListener("beforeunload", () => {
  if (thumbnailObjectUrl) URL.revokeObjectURL(thumbnailObjectUrl);
});
