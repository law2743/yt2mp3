"use strict";

const TOKEN_KEY = "yt2mp3_access_token";
const TOKEN_EXPIRY_KEY = "yt2mp3_token_expires_at";
const form = document.querySelector("#login-form");
const passwordInput = document.querySelector("#password");
const statusBox = document.querySelector("#login-status");
const apiBaseUrl = String(window.YT2MP3_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");

function showStatus(message) {
  statusBox.className = "status error";
  statusBox.textContent = message;
}

function existingSessionIsValid() {
  const token = sessionStorage.getItem(TOKEN_KEY);
  const expiry = Number(sessionStorage.getItem(TOKEN_EXPIRY_KEY));
  return Boolean(token && expiry && Date.now() < expiry);
}

if (existingSessionIsValid()) {
  window.location.replace("index.html");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusBox.classList.add("hidden");
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    if (!/^https?:\/\//.test(apiBaseUrl)) {
      throw new Error("前端尚未設定有效的後端網址。");
    }
    const response = await fetch(`${apiBaseUrl}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: passwordInput.value }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(body?.error?.message || "無法登入，請稍後再試。");
    }
    sessionStorage.setItem(TOKEN_KEY, body.access_token);
    sessionStorage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + body.expires_in * 1000));
    passwordInput.value = "";
    window.location.replace("index.html");
  } catch (error) {
    const message = error instanceof TypeError
      ? "無法連線到本機後端，請確認 Tailscale 與服務是否已啟動。"
      : error.message;
    showStatus(message);
  } finally {
    button.disabled = false;
  }
});
