"use strict";

const form = document.querySelector("#analyze-form");
const urlInput = document.querySelector("#url");
const statusBox = document.querySelector("#status");
const resultBox = document.querySelector("#result");
let pollTimer = null;
let pollCount = 0;

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

function showStatus(message, kind = "") {
  statusBox.className = `status ${kind}`.trim();
  statusBox.textContent = message;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("請先登入。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message || "服務暫時無法完成操作。");
  }
  return response.status === 204 ? null : response.json();
}

function rememberJob(jobId) {
  sessionStorage.setItem("job_id", jobId);
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
    const job = await request(`/api/jobs/${jobId}`);
    showStatus(stageLabels[job.stage] || "正在處理…");
    if (job.status === "failed") {
      showStatus(job.error?.message || "處理失敗。", "error");
      return;
    }
    if (["ready", "completed"].includes(job.status)) renderResult(job);
    if (!["ready", "completed", "failed", "cancelled", "expired"].includes(job.status)) {
      pollCount += 1;
      pollTimer = window.setTimeout(() => poll(jobId), pollCount < 10 ? 1000 : 2000);
    }
  } catch (error) {
    showStatus(error.message, "error");
    sessionStorage.removeItem("job_id");
  }
}

function confidenceText(value) {
  if (value >= 0.7) return "較高可信度";
  if (value >= 0.45) return "中等可信度，建議試聽確認";
  return "調性不明確，歌曲可能轉調或大／小調容易混淆";
}

function renderResult(job) {
  const source = job.source;
  const analysis = job.analysis;
  const alternatives = analysis.candidates.slice(1)
    .map((item) => `${item.key} ${Math.round(item.score * 100)}%`).join("、") || "無";
  const buttons = job.shift_options.map((option) => `
    <button class="shift-button" type="button" data-shift="${option.semitones}">
      <span>${option.label}</span><strong>${option.target_key}</strong>
    </button>`).join("");
  const downloads = job.outputs.map((shift) => {
    const option = job.shift_options.find((item) => item.semitones === shift);
    return `<a class="download" href="/api/jobs/${job.job_id}/download/${shift}">下載 ${option.label}・${option.target_key} MP3</a>`;
  }).join("");
  resultBox.innerHTML = `
    <div class="song-head">
      ${source.thumbnail_url ? `<img src="${source.thumbnail_url}" alt="影片縮圖">` : ""}
      <div><p class="eyebrow">分析結果</p><h2>${escapeHtml(source.title)}</h2>
      <p class="muted">${escapeHtml(source.uploader || "未知頻道")}・${formatDuration(source.duration_seconds)}</p></div>
    </div>
    <div class="key-summary"><div><span>原調</span><strong>${analysis.key || analysis.display_name}</strong></div>
      <div><span>可信度</span><strong>${Math.round(analysis.confidence * 100)}%</strong></div></div>
    <p>${confidenceText(analysis.confidence)}</p><p class="muted">其他可能：${alternatives}</p>
    <div class="shift-grid" aria-label="轉調選項">${buttons}</div>
    <div class="downloads">${downloads}</div>`;
  resultBox.classList.remove("hidden");
  resultBox.querySelectorAll(".shift-button").forEach((button) => {
    button.addEventListener("click", () => startTranspose(job.job_id, Number(button.dataset.shift)));
  });
}

async function startTranspose(jobId, semitones) {
  resultBox.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  showStatus("正在建立轉調工作…");
  try {
    const response = await request(`/api/jobs/${jobId}/transpose`, {
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

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
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

const savedJob = new URL(window.location.href).searchParams.get("job") || sessionStorage.getItem("job_id");
if (savedJob) poll(savedJob);
