# yt2mp3 — 產品與技術規格書

版本：1.1
日期：2026-06-20
目標讀者：Codex／開發者
開發目標：建立 Render 靜態前端與 Tailscale 私有本機 API，讓使用者貼上 YouTube 網址、分析歌曲調性，選擇升降半音後輸出 MP3。

> 文件定位：本文件保留為 v1.1 MVP 的歷史產品需求與驗收基準。本文內的舊 roadmap 不再代表現行 Phase 編號；現行部署、程式結構與 Phase 1～5 發展規劃請以 `../../README.md` 為準。

---

## 1. 專案摘要

本專案是一個個人使用優先的音訊轉調工具。使用者貼上 YouTube 影片網址後，系統分兩階段處理：

1. 取得影片音訊並分析原始調性（Key）。
2. 顯示原調前後各 2～3 個半音的選項；使用者選擇後，系統在不改變播放速度的前提下轉調，輸出可下載的 MP3。

MVP 不使用 Ollama 或生成式 AI。調性偵測、音訊轉換與轉調均使用專用的數位訊號處理工具。未來若加入人聲／伴奏分離或合音分析，可再將重運算工作轉送至使用者既有的 Tailscale 地端 GPU API。

### 1.1 核心原則

- 外層 Web 應用自行輕量開發，不 fork 大型下載器專案。
- 核心能力採用成熟開源元件：`yt-dlp`、`ffmpeg`、`librosa`、Rubber Band。
- 僅允許 YouTube 網址，不做通用型任意網站下載器。
- 不保存永久檔案；工作及產物到期後自動刪除。
- MVP 優先追求流程簡單、容易部署、容易除錯。
- 僅處理使用者擁有或已獲授權使用的內容。

---

## 2. 範圍

### 2.1 MVP 必須完成

- 單頁式繁體中文介面。
- 輸入並驗證 YouTube 一般影片或 `youtu.be` 網址。
- 取得影片標題、縮圖、長度與音訊。
- 偵測歌曲全局調性：根音＋Major／Minor。
- 顯示偵測可信度及最多 3 個候選調性。
- 顯示 5 或 7 個轉調按鈕：預設為 `-3` 至 `+3` 半音，包含原調。
- 每個按鈕同時顯示半音差及轉調後調名。
- 使用者點選後才執行轉調，避免預先產生全部版本。
- 轉調不得改變歌曲速度，成品長度誤差需在容許範圍內。
- 輸出 MP3，保留或重新寫入標題、作者及縮圖等 metadata。
- 顯示下載、分析、轉調、完成與失敗狀態。
- 自動清除暫存檔案。
- 後端 Docker 化，提供 Render Static Site、Tailscale 與 README 部署設定。

### 2.2 MVP 不做

- 使用者帳號、社群登入、付費功能。
- 播放清單、頻道批次下載、排程下載。
- 搜尋 YouTube 或顯示推薦影片。
- 任意網站 URL 下載。
- Apple Music／Spotify 串流處理。
- 自動判斷「最適合使用者唱的 Key」。
- 自動分離人聲、伴奏、主唱及合音。
- 自動生成和音譜、MIDI 或 MusicXML。
- 永久保存歷史紀錄。
- 同時預先產生 5～7 個 MP3。
- Ollama／LLM 整合。

### 2.3 後續階段

- Phase 2：視需要加入持久化 job store 或公開 Funnel 模式。
- Phase 3：使用 Demucs／UVR 做人聲與伴奏分離。
- Phase 4：建立個人音域資料，提供轉調建議。
- Phase 5：背景和聲分析、MIDI／樂譜草稿。

---

## 3. 名詞與轉調規則

### 3.1 用語

- `Key`：歌曲調性，例如 E Major、C# Minor。
- `半音（semitone）`：十二平均律的最小標準位移單位。
- `shift`：轉調的半音數，負數為降、正數為升。
- `原調`：`shift = 0`。
- `job`：一次從分析到下載完成的工作。

介面一律使用「半音」，不得使用語意不明的「升／降一個 Key」。

### 3.2 調名計算

- 根音依十二音循環計算：`(root_index + shift) mod 12`。
- Major／Minor 模式在轉調後保持不變。
- 顯示名稱採易讀的降記號優先表：
  `C, Db, D, Eb, E, F, Gb, G, Ab, A, Bb, B`。
- 若原始分析結果為升記號，可在詳細資訊保留等音名稱，例如 `Db (C#)`。
- 範例：E Major 降 2 半音為 D Major；C# Minor 升 1 半音為 D Minor。

### 3.3 按鈕範圍

- 環境變數 `SHIFT_RANGE` 控制範圍，只允許 `2` 或 `3`。
- 預設 `SHIFT_RANGE=3`，顯示 7 個按鈕：`-3, -2, -1, 0, +1, +2, +3`。
- 原調按鈕仍需輸出標準化 MP3，但不呼叫 Rubber Band。

---

## 4. 使用者流程

### 4.1 正常流程

1. 使用者開啟首頁。
2. 貼上 YouTube 網址。
3. 點擊「分析歌曲」。
4. 前端建立分析工作並顯示進度：
   - 讀取影片資訊
   - 取得音訊
   - 分析調性
5. 系統顯示：
   - 影片縮圖
   - 歌曲標題
   - 長度
   - 偵測結果，例如 `E Major`
   - 可信度，例如 `72%`，以及候選調性
   - 5 或 7 個轉調按鈕
6. 使用者點選，例如「降 2 半音・D Major」。
7. 系統顯示轉調進度，並暫時停用所有轉調按鈕。
8. 完成後顯示：
   - 下載 MP3
   - 重新選擇其他調
   - 分析另一首歌曲
9. 使用者下載檔案。
10. 工作建立後達 TTL，系統刪除來源與輸出檔案。

### 4.2 重複選擇

- 同一 job 可在 TTL 內再選其他半音數。
- 若該 shift 已產生且檔案仍存在，直接回傳既有成品。
- 同一 job 同時間只允許一個轉調工作。
- MVP 最多保留最近 2 個輸出版本；超過後刪除最舊成品，但保留分析來源直到 job 過期。

### 4.3 重新整理頁面

- 建立 job 後將 `job_id` 存於 URL query 或 `sessionStorage`。
- 頁面重新整理後嘗試讀取 job 狀態。
- 本機後端重啟造成 job 遺失時，顯示「暫存工作已失效，請重新分析」，不得無限重試。

---

## 5. UI／UX 規格

### 5.1 頁面結構

單一頁面分成四區：

1. 標題與簡短說明。
2. URL 輸入與「分析歌曲」按鈕。
3. 進度／錯誤訊息區。
4. 分析結果與轉調／下載區。

### 5.2 初始畫面文案

- 標題：`YouTube 歌曲轉調工具`
- 說明：`貼上你有權使用的 YouTube 影片網址，分析原調後選擇升降半音並輸出 MP3。`
- Placeholder：`https://www.youtube.com/watch?v=...`
- 主按鈕：`分析歌曲`
- 授權提示：`請僅處理你擁有或已獲授權使用的內容。`

### 5.3 分析結果

範例：

```text
歌曲：Example Song
原調：E Major
可信度：72%
其他可能：C# Minor 18%、A Major 10%

[降 3 半音・Db Major]
[降 2 半音・D Major]
[降 1 半音・Eb Major]
[原調・E Major]
[升 1 半音・F Major]
[升 2 半音・Gb Major]
[升 3 半音・G Major]
```

### 5.4 可信度呈現

- `>= 0.70`：顯示「較高可信度」。
- `0.45～0.69`：顯示「中等可信度，建議試聽確認」。
- `< 0.45`：顯示「調性不明確，歌曲可能轉調或大／小調容易混淆」。
- 無論可信度多低，只要分析成功仍可使用半音按鈕。
- 不得宣稱偵測結果 100% 正確。

### 5.5 行動裝置

- iPhone Safari 及 Chrome 為主要驗收裝置之一。
- 轉調按鈕在手機上單欄或雙欄顯示，每個觸控區高度至少 44px。
- 不依賴 hover 才能顯示重要資訊。
- 下載按鈕必須使用一般連結，讓 iOS 可開啟或存入「檔案」。

### 5.6 無障礙

- 所有輸入欄位具有 label。
- 錯誤與進度區使用 `aria-live`。
- 按鈕具清楚文字，不以顏色作為唯一狀態提示。
- 鍵盤可完成 URL 輸入、分析、選調與下載。

---

## 6. 系統架構

### 6.1 MVP 架構

```text
Render Static Site
  │ 提供 HTML / CSS / JavaScript
  ▼
Browser（同一 tailnet 裝置）
  │ HTTPS / Bearer token / JSON / polling
  ▼
Tailscale Serve
  │ loopback
  ▼
本機 FastAPI
  ├─ Job Manager（記憶體）
  ├─ 單機背景佇列
  ├─ yt-dlp
  ├─ ffmpeg / ffprobe
  ├─ librosa Key Analyzer
  └─ rubberband-cli
       │
       ▼
  /tmp/yks/<job_id>/
```

### 6.2 拆分原則

- Render 僅提供可免費部署的純靜態檔案。
- 下載、分析與轉調留在住宅網路的本機後端，避開雲端 CPU 成本與資料中心 IP 限制。
- 前端使用原生 HTML／CSS／JavaScript，不需要 React 或 Node runtime。
- 跨網域只允許設定中的精確 Render origin，不允許 `*`。

### 6.3 程序限制

- Uvicorn 僅啟動 1 個 worker，避免多程序各自擁有不同 job 記憶體。
- 背景工作必須由單一 queue 管理，不可在 request handler 內同步完成整首轉檔。
- 外部程式一律透過 `asyncio.create_subprocess_exec` 或等效安全方式呼叫。
- 禁止使用 `shell=True`，避免 URL 或檔名造成 command injection。
- 每個 subprocess 都必須有 timeout，逾時時終止整個 process group。

### 6.4 Tailscale 模式

- MVP 使用 `tailscale serve --bg 8000`，只有同一 tailnet 裝置能存取。
- FastAPI 或 Docker port 只綁定 `127.0.0.1`，不直接暴露 LAN／WAN。
- Funnel 不屬於 MVP 預設；若啟用，需重新評估公開濫用與頻寬限制。

---

## 7. 技術選型

### 7.1 後端

- Python 3.12。
- FastAPI。
- Uvicorn。
- Pydantic v2。
- 標準 logging，輸出 JSON 或至少包含 `job_id`、stage、duration、error_code。

### 7.2 音訊工具

- `yt-dlp`：讀取 YouTube metadata 與最佳音訊來源。
- `ffprobe`：檢查時長、codec、sample rate 與檔案有效性。
- `ffmpeg`：轉 WAV、標準化 MP3、寫入 metadata 與封面。
- `librosa`：chroma 特徵及調性分析。
- `rubberband-cli`：不改速度的 pitch shift。

### 7.3 Key 偵測方法

MVP 使用可測試的傳統 MIR 演算法，不使用 LLM：

1. 將音訊轉為 mono WAV，建議 22,050 Hz。
2. 排除開頭及結尾的低能量／靜音片段。
3. 計算 `chroma_cqt` 或經驗證較穩定的 chroma 特徵。
4. 對時間軸做能量加權聚合，得到 12 維 pitch-class profile。
5. 與 Krumhansl–Schmuckler Major／Minor 共 24 個旋轉模板計算相關係數。
6. 依分數排序，回傳前三名候選。
7. 將第一名與其他候選的分數差正規化為 UI 可信度；此值是內部相對指標，不宣稱為統計機率。

分析器必須封裝成介面，方便日後替換為 Essentia 或 libKeyFinder：

```python
class KeyAnalyzer(Protocol):
    def analyze(self, audio_path: Path) -> KeyAnalysisResult: ...
```

`KeyAnalysisResult` 至少包含：

- `root_index: int`，0～11。
- `root_name: str`。
- `mode: Literal["major", "minor"]`。
- `display_name: str`。
- `confidence: float`，0～1。
- `candidates: list[KeyCandidate]`，最多 3 筆。
- `algorithm_version: str`。

### 7.4 轉調方法

非零 shift：

```text
source audio
  → ffmpeg 解碼成 PCM WAV
  → rubberband-cli --pitch <frequency_ratio>
  → ffmpeg libmp3lame 輸出 MP3
  → 寫入 metadata／封面
```

其中：

```text
frequency_ratio = 2 ** (semitones / 12)
```

實作時應優先使用 Rubber Band 官方支援的 semitone／frequency 參數，並以工具實際版本說明為準。轉調後不得透過改變播放速度來假裝改 pitch。

`shift = 0`：略過 Rubber Band，直接由來源音訊標準化輸出 MP3。

### 7.5 MP3 輸出

- Codec：`libmp3lame`。
- 預設品質：VBR V0；若環境相容性有問題可改 256 kbps CBR。
- Sample rate：44.1 kHz。
- Channels：保留 stereo；若來源為 mono 則不強制偽造 stereo。
- ID3：
  - Title：原標題＋轉調後綴，例如 `Example Song [降2半音・D Major]`。
  - Artist／Uploader：來源 uploader，若可取得。
  - Comment：`Transposed by yt2mp3`。
  - Cover：來源縮圖轉為相容的 JPEG 後嵌入。
- 檔名：`<sanitized-title>_<shift-label>_<target-key>.mp3`。
- 檔名需移除路徑字元、控制字元及特殊 shell 字元，最長 120 字元。

---

## 8. API 規格

所有 API 回應使用 JSON，下載端點除外。錯誤格式統一為：

```json
{
  "error": {
    "code": "INVALID_YOUTUBE_URL",
    "message": "請輸入有效的 YouTube 影片網址。",
    "retryable": false
  }
}
```

### 8.1 建立分析工作

`POST /api/jobs/analyze`

Request：

```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID"
}
```

Response `202 Accepted`：

```json
{
  "job_id": "uuid-v4",
  "status": "queued",
  "status_url": "/api/jobs/uuid-v4"
}
```

行為：

- 先做 URL 與容量限制的快速驗證。
- 建立 job directory 與 queue item。
- 不等待下載或分析完成才回應。
- 同一 client 在短時間重複提交相同 URL，可回傳尚未過期的既有 job。

### 8.2 查詢工作狀態

`GET /api/jobs/{job_id}`

分析中範例：

```json
{
  "job_id": "uuid-v4",
  "status": "analyzing",
  "stage": "detecting_key",
  "progress": 70,
  "created_at": "2026-06-19T12:00:00Z",
  "expires_at": "2026-06-19T13:00:00Z"
}
```

分析完成範例：

```json
{
  "job_id": "uuid-v4",
  "status": "ready",
  "stage": "awaiting_selection",
  "progress": 100,
  "source": {
    "video_id": "VIDEO_ID",
    "title": "Example Song",
    "uploader": "Example Channel",
    "duration_seconds": 245,
    "thumbnail_url": "/api/jobs/uuid-v4/thumbnail"
  },
  "analysis": {
    "key": "E Major",
    "root_index": 4,
    "mode": "major",
    "confidence": 0.72,
    "candidates": [
      {"key": "E Major", "score": 0.72},
      {"key": "C# Minor", "score": 0.18},
      {"key": "A Major", "score": 0.10}
    ],
    "algorithm_version": "librosa-ks-v1"
  },
  "shift_options": [
    {"semitones": -3, "label": "降 3 半音", "target_key": "Db Major"},
    {"semitones": -2, "label": "降 2 半音", "target_key": "D Major"},
    {"semitones": -1, "label": "降 1 半音", "target_key": "Eb Major"},
    {"semitones": 0, "label": "原調", "target_key": "E Major"},
    {"semitones": 1, "label": "升 1 半音", "target_key": "F Major"},
    {"semitones": 2, "label": "升 2 半音", "target_key": "Gb Major"},
    {"semitones": 3, "label": "升 3 半音", "target_key": "G Major"}
  ]
}
```

### 8.3 建立轉調工作

`POST /api/jobs/{job_id}/transpose`

Request：

```json
{
  "semitones": -2,
  "bitrate_kbps": 192
}
```

Response `202 Accepted`：

```json
{
  "job_id": "uuid-v4",
  "status": "transposing",
  "semitones": -2,
  "target_key": "D Major",
  "bitrate_kbps": 192
}
```

驗證：

- job 必須為 `ready`、`completed` 或可再次轉調的狀態。
- `semitones` 必須為整數且位於 `-SHIFT_RANGE` 至 `+SHIFT_RANGE`。
- `bitrate_kbps` 必須為 `128`、`192` 或 `256`。
- 來源檔案必須存在且未過期。
- 正在轉調時回傳 `409 JOB_BUSY`，不可同時啟動第二個轉調。

### 8.4 下載

`GET /api/jobs/{job_id}/download/{semitones}?bitrate_kbps=192`

- 僅 job 擁有已完成的指定版本時可下載。
- 回傳 `Content-Type: audio/mpeg`。
- 使用正確的 `Content-Disposition` 與 UTF-8 檔名。
- 建議 `Cache-Control: private, no-store`。
- 不可接受任意檔案路徑；實體路徑只能由 job_id 與白名單 shift 內部組合。

### 8.5 刪除工作

`DELETE /api/jobs/{job_id}`

- 終止該 job 尚在執行的 subprocess。
- 刪除 job 目錄與記憶體狀態。
- 成功回傳 `204 No Content`。

### 8.6 健康檢查

`GET /health`

Response：

```json
{
  "status": "ok",
  "version": "1.0.0",
  "dependencies": {
    "yt_dlp": true,
    "ffmpeg": true,
    "ffprobe": true,
    "rubberband": true
  }
}
```

健康檢查不得呼叫 YouTube、不得執行完整音訊測試，也不得洩漏路徑、token 或環境變數。

---

## 9. Job 狀態機

允許狀態：

```text
queued
  → fetching_metadata
  → downloading
  → preparing_audio
  → analyzing
  → ready
  → transposing
  → completed

任一處 → failed
任一非終態 → cancelled
ready/completed → expired
```

規則：

- `failed` 必須包含公開錯誤代碼與安全訊息；內部 log 可保留詳細 stderr。
- `completed` 後仍可再次進入 `transposing`，產生不同 shift。
- job 更新時間必須使用 UTC ISO 8601。
- 前端每 1 秒 polling；連續 10 次後改為每 2 秒，完成後停止。
- 單一階段不應回報虛假的精確百分比；無法取得真實進度時顯示階段式進度。

---

## 10. YouTube 取得規格

### 10.1 URL 白名單

僅允許：

- `https://www.youtube.com/watch?v=<id>`
- `https://youtube.com/watch?v=<id>`
- `https://youtu.be/<id>`
- `https://m.youtube.com/watch?v=<id>`

可接受 YouTube 分享時附帶的無害 query，但需正規化為 canonical video ID。MVP 拒絕：

- playlist／channel URL。
- live stream／尚未開始直播。
- Shorts 若無法穩定辨識則先拒絕；若支援，需正規化為單一 video ID。
- 非 YouTube hostname。
- `file://`、內網 IP、localhost 或任何自訂 scheme。

### 10.2 yt-dlp 安全呼叫

- 僅將正規化後的 YouTube canonical URL 傳給 yt-dlp。
- 禁止將使用者輸入拼入 shell command 字串。
- 關閉 playlist：`--no-playlist`。
- 先取得 metadata 並套用時長限制，再下載音訊。
- 使用明確 output template，且檔案必須留在 job directory。
- 對 yt-dlp 設定合理 socket timeout、retry 次數與總 timeout。
- MVP 不接受使用者上傳 cookies。
- 若未設定管理者 cookies，遇到登入／bot 驗證直接回傳可理解錯誤。

### 10.3 限制

預設值可由環境變數調整：

- 最長影片：15 分鐘。
- 最大來源檔：150 MB。
- metadata timeout：30 秒。
- download timeout：5 分鐘。
- analysis timeout：3 分鐘。
- transpose timeout：10 分鐘。
- 同時處理工作數：1。
- 佇列長度：5。

### 10.4 雲端 IP 限制

需辨識以下類型並使用獨立錯誤碼：

- YouTube 要求登入或確認不是機器人。
- HTTP 403。
- HTTP 429／rate limit。
- 影片地區限制或私人影片。
- 找不到可用音訊格式。

不得對使用者顯示完整 yt-dlp command、cookies 路徑或內部 stack trace。

---

## 11. 暫存與生命週期

### 11.1 目錄

```text
/tmp/yks/<job_id>/
  metadata.json
  source.<ext>
  analysis.wav
  thumbnail.jpg
  output/
    shift_-2.mp3
```

- `job_id` 必須為系統產生的 UUID，不使用影片標題作為資料夾名稱。
- 所有路徑在使用前需確認 resolve 後仍位於 job root。

### 11.2 TTL

- 預設 `JOB_TTL_MINUTES=60`。
- 以 job 建立時間計算，不因輪詢無限延長。
- 每 5 分鐘執行 cleanup task。
- 服務啟動時先掃描並刪除過期 job 目錄。
- 清除失敗需記錄 log，但不得使整個服務停止。

### 11.3 本機暫存檔案

- 不依賴檔案跨重啟存在。
- 重啟後所有記憶體 job 視為失效。
- 暫存檔只存在本機 `WORK_ROOT`，不傳至 Render。
- 若未來需要跨重啟，改用 Redis／PostgreSQL 儲存 job 狀態，R2／S3 儲存音檔。

---

## 12. 安全、隱私與濫用防護

### 12.1 基本安全

- 所有輸入使用 Pydantic 驗證。
- URL 使用 allowlist，不只做字串包含 `youtube.com` 的判斷。
- subprocess 禁止 `shell=True`。
- 檔案路徑不可由使用者直接控制。
- 對輸出檔名做 sanitize。
- 限制 request body 大小。
- 回應不得包含本機絕對路徑。
- production 關閉 FastAPI debug 與詳細錯誤頁。
- dependency 版本鎖定，並提供更新方式。

### 12.2 存取控制

本服務預設為私人使用：

- 登入密碼由 `APP_PASSWORD` 提供，登入成功簽發短效 Bearer token。
- Token signing key 由 `TOKEN_SECRET` 提供，不可寫死或放進靜態前端。
- Token 儲存在瀏覽器 `sessionStorage`，關閉分頁後移除。
- API 端點除 `/health` 與 `/api/auth/login` 外皆需驗證 Bearer token。
- MP3 與縮圖以授權 `fetch` 取得，不提供匿名媒體 URL。

不得只靠隱藏網址當作安全措施。

### 12.3 Rate limit

- 每個 token owner／來源 IP：每分鐘最多 5 次建立工作。
- 同一 token owner 同時最多 1 個 active job。
- 登入失敗依來源位址限制嘗試次數。
- queue 已滿回傳 `429 QUEUE_FULL` 或 `503 SERVICE_BUSY`。
- 記錄限制事件，但避免長期保存完整 IP；可記錄截斷或雜湊值。

### 12.4 隱私

- 不永久保存輸入 URL、音訊或下載紀錄。
- log 不記錄密碼、Bearer token 或 cookies。
- URL 可只記錄 video ID。
- README 與 UI 明確說明暫存 TTL。

### 12.5 內容授權

- UI 必須顯示使用者僅能處理有權使用的內容。
- 不宣稱本工具可合法下載所有 YouTube 內容。
- 不加入繞過 DRM、付費牆、會員限定或地區限制的功能。

---

## 13. 錯誤碼

至少實作：

| HTTP | Code | 顯示訊息 |
|---:|---|---|
| 400 | `INVALID_YOUTUBE_URL` | 請輸入有效的 YouTube 單一影片網址。 |
| 400 | `PLAYLIST_NOT_SUPPORTED` | 目前不支援播放清單。 |
| 400 | `VIDEO_TOO_LONG` | 影片超過可處理的長度限制。 |
| 401 | `AUTH_REQUIRED` | 請先登入。 |
| 404 | `JOB_NOT_FOUND` | 暫存工作不存在或已失效。 |
| 409 | `JOB_BUSY` | 這首歌曲正在處理中，請稍候。 |
| 413 | `SOURCE_TOO_LARGE` | 音訊檔案超過處理上限。 |
| 422 | `INVALID_SHIFT` | 請選擇畫面提供的升降半音數。 |
| 429 | `RATE_LIMITED` | 操作過於頻繁，請稍後再試。 |
| 429 | `YOUTUBE_RATE_LIMITED` | YouTube 暫時限制此服務，請稍後再試。 |
| 502 | `YOUTUBE_AUTH_REQUIRED` | YouTube 要求額外驗證，目前無法取得此影片。 |
| 502 | `AUDIO_FORMAT_UNAVAILABLE` | 找不到可處理的音訊格式。 |
| 500 | `ANALYSIS_FAILED` | 無法判斷歌曲調性，但未洩漏內部細節。 |
| 500 | `TRANSPOSE_FAILED` | 音訊轉調失敗，請重新嘗試。 |
| 503 | `SERVICE_BUSY` | 目前處理工作較多，請稍後再試。 |
| 504 | `PROCESS_TIMEOUT` | 處理時間超過限制，請改用較短的影片。 |

對於 `ANALYSIS_FAILED`，若來源音訊有效，可考慮允許使用者略過調性偵測，直接選擇 `-3～+3` 半音；此為 SHOULD，不是 MVP MUST。

---

## 14. 設定與環境變數

```text
APP_ENV=production
APP_PASSWORD=<secret>
TOKEN_SECRET=<long-random-secret>
CORS_ALLOWED_ORIGINS=https://your-frontend.onrender.com
ACCESS_TOKEN_TTL_MINUTES=60
SHIFT_RANGE=3
JOB_TTL_MINUTES=60
MAX_VIDEO_DURATION_SECONDS=900
MAX_SOURCE_MB=150
MAX_QUEUE_SIZE=5
MAX_CONCURRENT_JOBS=1
WORK_ROOT=/tmp/yks
LOG_LEVEL=INFO
YTDLP_COOKIES_FILE=
```

規則：

- production 缺少 `APP_PASSWORD`、`TOKEN_SECRET` 或有效 CORS origin 時啟動失敗。
- `YTDLP_COOKIES_FILE` 僅供管理者未來自行掛載，MVP 預設空白。
- secrets 不得進版控、Docker image 或 log。
- 提供 `.env.example`，只放假值與說明。

---

## 15. 建議專案結構

```text
yt2mp3/
├─ app/
│  ├─ main.py
│  ├─ config.py
│  ├─ models.py
│  ├─ api/
│  │  ├─ auth.py
│  │  └─ jobs.py
│  ├─ services/
│  │  ├─ job_manager.py
│  │  ├─ youtube.py
│  │  ├─ audio.py
│  │  ├─ key_analyzer.py
│  │  ├─ transposer.py
│  │  └─ cleanup.py
├─ frontend/
│  ├─ index.html
│  ├─ login.html
│  ├─ app.js
│  ├─ auth.js
│  ├─ config.js
│  └─ styles.css
├─ tests/
│  ├─ unit/
│  │  ├─ test_url_validation.py
│  │  ├─ test_key_mapping.py
│  │  ├─ test_key_analyzer.py
│  │  ├─ test_filename_sanitization.py
│  │  └─ test_job_state.py
│  ├─ integration/
│  │  ├─ test_analyze_pipeline.py
│  │  └─ test_transpose_pipeline.py
│  └─ fixtures/
│     └─ README.md
├─ scripts/
│  └─ smoke_test.sh
├─ .env.example
├─ .gitignore
├─ Dockerfile
├─ render.yaml
├─ pyproject.toml
├─ README.md
└─ LICENSE
```

---

## 16. Docker 與 Render 部署

### 16.1 Dockerfile 要求

- 使用明確版本的 Python slim base image。
- 安裝 `ffmpeg`、`rubberband-cli` 與必要系統 library。
- 使用非 root 使用者執行應用。
- Python dependency 使用 lock file 或鎖定版本。
- 不將測試音檔、cookies、`.env` 打包進 image。
- 啟動前建立可寫入的 `WORK_ROOT`。
- 啟動命令使用單一 worker，例如：
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`。

### 16.2 render.yaml

- 建立新的 Static Site，使用 `runtime: static`。
- `staticPublishPath` 指向 `./frontend`。
- `BACKEND_API_URL` 在 build 時寫入公開的 `config.js`。
- 不在 Render 宣告 `APP_PASSWORD` 或 `TOKEN_SECRET`。
- `autoDeploy` 可依使用者偏好設定。

### 16.3 冷啟動

- 靜態首頁應能獨立載入；本機後端離線時顯示明確提示。
- 不在 app startup 時載入大型模型或呼叫外部網站。
- Key analyzer 的 Python library 可 lazy import，但首次分析需顯示正常進度。

---

## 17. 測試規格

### 17.1 單元測試

必須涵蓋：

- YouTube URL 正規化與拒絕惡意／非白名單 URL。
- 十二音 Major／Minor 在 `-3～+3` 的全部 mapping。
- 跨八度 modulo 行為，例如 C 降 1 為 B。
- 檔名 sanitize 與長度限制。
- Job 合法／非法狀態轉換。
- TTL 判定與 cleanup。
- API request schema。
- yt-dlp stderr 至公開錯誤碼的 mapping。
- subprocess timeout 與取消。

### 17.2 調性分析測試

使用自行產生或授權的短音訊 fixture：

- C Major 和弦進行。
- A Minor 和弦進行。
- E Major 和弦進行。
- 靜音檔案。
- 白噪音。
- 時間太短的音訊。

預期：

- 清楚的合成和弦進行應判斷為正確或相對大小調候選前二名。
- 靜音／無調性輸入不得回傳高可信度結果。
- 演算法結果應可重現。

### 17.3 轉調測試

- 產生 A4 = 440 Hz 測試音。
- 升 1 半音後主頻應接近 466.16 Hz。
- 降 2 半音後主頻應接近 392.00 Hz。
- 允許合理 DSP 誤差，例如 ±0.5%。
- 轉調前後長度誤差不超過 100 ms 或原長度 0.1%，取較寬者。
- MP3 可由 ffprobe 解碼且 metadata 存在。

### 17.4 整合測試

- 測試時 mock yt-dlp metadata／download，不依賴 YouTube 網路。
- 完整跑過：建立 job → 分析 → 選 shift → 完成 → 下載。
- 測試重複點擊同一 shift 使用 cache。
- 測試 job 過期後下載回傳 404／410。
- 測試 queue full、process timeout、取消及 cleanup。

### 17.5 Smoke test

- 可選用開發者自己上傳、Creative Commons 或明確授權的短 YouTube 測試影片。
- Smoke test 不應在每次 CI 執行，以避免外部服務不穩造成假失敗。
- CI 不保存產生的媒體檔案。

---

## 18. 驗收條件

### 18.1 功能驗收

- [ ] iPhone Safari 與桌面 Chrome 可貼上有效 YouTube URL。
- [ ] 分析請求立即回傳 job，不造成瀏覽器 request timeout。
- [ ] 可顯示影片標題、縮圖、長度、原調與可信度。
- [ ] 預設準確顯示 7 個 `-3～+3` 半音按鈕。
- [ ] 每個按鈕顯示正確的目標調名。
- [ ] 點選後只生成所選版本。
- [ ] 轉調後速度與歌曲長度保持一致。
- [ ] MP3 可在 iPhone、macOS Music 與一般播放器播放。
- [ ] 下載檔名、ID3 標題與封面正確。
- [ ] 同一 shift 重複選擇不重做轉檔。
- [ ] 過期檔案會自動清除。

### 18.2 安全驗收

- [ ] 非 YouTube URL 被拒絕。
- [ ] playlist 與超長影片被拒絕。
- [ ] 使用含 shell metacharacters 的輸入不會被執行。
- [ ] 使用 `../` 或偽造 job_id 無法讀取任意檔案。
- [ ] 未登入無法呼叫工作 API。
- [ ] secrets、cookies 與本機路徑不出現在 log 或 API response。
- [ ] queue 與 rate limit 有效。

### 18.3 部署驗收

- [ ] `docker build` 成功。
- [ ] 本機 `docker compose up` 後 `/health` 正常。
- [ ] Render 可由 `render.yaml` 部署。
- [ ] 手機連入同一 tailnet 後可登入、輪詢及下載。
- [ ] README 包含本機啟動、測試、Render 部署與限制說明。

---

## 19. 可觀測性

每個 stage 至少記錄：

- timestamp。
- level。
- app version。
- job_id。
- video_id。
- stage。
- elapsed_ms。
- result／error_code。

不得記錄：

- APP_PASSWORD。
- Bearer token。
- TOKEN_SECRET。
- YouTube cookies。
- 完整 stack trace 至公開 response。

建議 metrics：

- 建立 job 數。
- 分析成功率。
- 轉調成功率。
- 各階段平均耗時。
- YouTube 403／429／驗證錯誤數。
- queue 長度。
- cleanup 刪除檔案數與失敗數。

MVP 可先以結構化 log 取代 Prometheus。

---

## 20. 效能目標

以一首 4 分鐘歌曲為基準，目標不是硬性 SLA：

- metadata：一般情況 10 秒內。
- 下載＋準備：依網路而定，目標 60 秒內。
- Key 分析：本機 CPU 目標 60 秒內。
- 轉調＋MP3：本機 CPU 目標 2～4 分鐘內。
- API 非媒體處理端點 p95：500 ms 內。
- 記憶體峰值應低於部署方案限制，處理時避免同時將完整 WAV 複製多份至 RAM。

若實測無法達成，優先調整：

1. 降低分析 WAV sample rate，不降低最終輸出音質。
2. 分段／取樣做 Key 分析。
3. 降低併發數。
4. 調整本機硬體或工作限制。

---

## 21. 授權注意事項

- 專案需建立 `THIRD_PARTY_NOTICES.md`，列出所有直接使用的外部工具及授權。
- Rubber Band 為 GPL／另有商業授權；若應用程式與其整合方式觸發 GPL 義務，專案必須採相容授權並公開相應原始碼，或另購商業授權。
- `yt-dlp`、ffmpeg build、編碼器與 Python dependencies 的授權需逐一確認。
- 開發前應決定本專案是否公開為 GPL 相容專案。
- 若未完成授權確認，不得宣稱可直接作閉源商業服務。

這一節是工程規格提醒，不構成法律意見。

---

## 22. Codex 開發執行要求

Codex 應依以下順序實作，每階段皆需可執行與測試：

1. 建立 FastAPI 純 API 骨架、Bearer 登入與 `/health`。
2. 實作 URL allowlist、canonicalization 與單元測試。
3. 實作 job model、狀態機、記憶體 queue、TTL cleanup。
4. 實作 yt-dlp metadata／download adapter，完整封裝 subprocess 與錯誤 mapping。
5. 實作 ffmpeg preparation 與 ffprobe 驗證。
6. 實作可替換的 librosa KeyAnalyzer 與合成音訊測試。
7. 實作調名 mapping 與 API shift options。
8. 實作 Rubber Band 轉調、MP3 metadata／封面及頻率測試。
9. 完成 Render 靜態前端 polling、授權下載與手機版。
10. 補齊 rate limit、取消、timeout、路徑安全與 cleanup。
11. 建立 Dockerfile、Compose、Tailscale、render.yaml、README 與 THIRD_PARTY_NOTICES。
12. 執行完整測試、Docker smoke test 與 Render 部署前檢查。

### 22.1 程式品質要求

- 所有外部工具透過 adapter／service 封裝，API route 不直接組 command。
- 核心邏輯必須具 type hints。
- 不用 `shell=True`。
- 不將單例狀態散落在多個 module。
- 不以 sleep 模擬進度。
- 不捕捉所有例外後靜默忽略。
- 不在測試中下載未授權歌曲。
- 不為了「先跑起來」而停用 SSL 驗證或 URL 安全檢查。
- 若實際工具參數與本規格示例不同，以官方版本文件及自動測試結果為準，並在 README 說明。

### 22.2 完工交付物

- 可執行的完整 source code。
- `README.md`。
- `.env.example`。
- `Dockerfile`。
- `render.yaml`。
- `LICENSE`。
- `THIRD_PARTY_NOTICES.md`。
- 單元與整合測試。
- 測試結果摘要。
- 已知限制清單。
- Render Static Site 與 Tailscale Serve 部署步驟。

---

## 23. Definition of Done

只有同時滿足以下條件，MVP 才算完成：

1. 完整走通「貼 URL → 分析 Key → 選擇半音 → 下載 MP3」。
2. 在本機 Docker 環境成功處理至少一個自有／授權 YouTube 測試影片。
3. 所有必要單元與整合測試通過。
4. 安全驗收項目通過，特別是 URL、subprocess 與檔案路徑安全。
5. 靜態前端可部署至 Render，本機 API 可透過 Tailscale Serve 通過 `/health`。
6. iPhone Safari 可正常操作與下載。
7. 已清楚揭露調性偵測準確度、本機暫存、上傳頻寬及內容授權限制。
8. 無任何 secret、cookies、測試音檔或產出 MP3 進入 Git repository。

---

## 24. 已知風險與決策

| 風險 | 影響 | MVP 決策 |
|---|---|---|
| YouTube 限制來源 IP | 無法取得部分影片 | 顯示明確錯誤，不繞過平台驗證 |
| Key 偵測存在相對大小調／轉調誤判 | 顯示調名可能不準 | 顯示可信度與候選；半音轉調仍可正常使用 |
| 本機 CPU 轉調較慢 | 使用者等待 | 單併發、背景 job、進度提示、限制片長 |
| 本機重啟造成工作消失 | 需重新分析 | MVP 接受 ephemeral 設計並清楚提示 |
| GPL 元件授權 | 影響閉源商用 | MVP 採開源方向；商業化前重新審查 |
| 服務被濫用 | 頻寬、封鎖與法律風險 | Tailscale Serve、token、rate limit、單 owner active job |
| 完整 WAV 佔用大量磁碟 | 本機空間不足 | 限制片長與來源大小、TTL cleanup、控制併發 |

---

## 25. 最終產品定位

本 MVP 是「個人使用、流程明確、可部署與可擴充」的 YouTube 音訊調性分析與轉調工具，不是通用下載站，也不是 AI 音樂平台。核心價值是：

> 使用者不需要先懂原曲調性，只需貼上有權使用的影片網址，即可看到可能的原調，並用清楚的半音選項輸出適合練唱的 MP3。
