# yt2mp3

yt2mp3 是一套私人使用、行動裝置友善的練唱素材生成工具。使用者貼上有權處理的 YouTube 影片網址後，系統會分析歌曲可能的全局調性，提供升降 Key MP3，並逐步加入人聲／伴奏分離、人聲音高追蹤、練唱旋律草稿與伴奏練唱功能。

目前穩定功能以 YouTube 音訊取得、全局調性分析、升降 Key 與 MP3 輸出為主；Host GPU 模式可提供 Demucs stems、RMVPE frame-level vocal pitch、旋律草稿與 MIDI，但仍屬私人練唱輔助用途，不定位為正式樂譜或準確扒譜結果。

目前版本使用 `yt-dlp`、FFmpeg、librosa、Rubber Band、Demucs 與 RMVPE，不使用生成式 AI。請僅處理自己擁有或已獲授權使用的內容；本專案不繞過 DRM、私人影片、地區限制或 YouTube 的存取控制。

## 目前功能

- 驗證 YouTube 單一影片網址並取得音訊與縮圖
- 使用 Krumhansl–Schmuckler profile 分析全局調性
- 顯示原調、可信度、候選調性及前後 2～3 個半音選項
- 使用 Rubber Band 轉調，維持原本速度
- 輸出 128、192 或 256 kbps MP3
- 使用 RMVPE vocal pitch 產生人聲旋律預覽、Melody JSON 與 MIDI
- 在 WSL host-native GPU 模式下，透過 Demucs 產生 `vocals.wav` 與 `accompaniment.wav`
- 在 WSL host-native GPU 模式下，透過 RMVPE 從 vocals stem 輸出 `analysis/pitch/vocal_pitch.json`
- Bearer token、job 擁有權、登入限流與嚴格 CORS
- 暫存工作排程、取消、TTL 清除及最近兩個輸出快取
- Render 靜態前端與 Tailscale 私有地端 API

## 架構

前端與後端已透過 HTTP API 分離，但目前仍放在同一個 repository，方便共同開發與版本管理：

```text
Render 或本機靜態前端
          │ HTTPS / JSON / polling
          ▼
FastAPI API（Mac 或 WSL 地端）
  ├─ Job manager：工作生命週期、擁有權、TTL
  ├─ Task queue：背景工作排程邊界
  ├─ Pipelines：調性分析、主旋律分析與轉調流程
  ├─ Model backends：Demucs / RMVPE subprocess adapters
  └─ Job artifacts：來源、分析產物與輸出檔案
```

主要目錄：

```text
app/
  api/                  FastAPI routes 與認證
  models/               API、job 與音樂分析資料模型
  services/
    artifacts.py        job 暫存目錄與產物路徑
    job_manager.py      job registry、生命週期與 worker
    task_queue.py       可替換的工作佇列邊界
    gpu_subprocess_env.py  GPU subprocess 環境隔離 helper
    model_backends/     Demucs / RMVPE 等模型 backend adapter
    pipelines/          analyze、melody、transpose 等流程
    audio.py            FFmpeg 與 Rubber Band 音訊處理
    key_analyzer.py     librosa 調性分析
    youtube.py          yt-dlp adapter
docs/                   工程文件、GPU venv/env 策略與歷史規格
frontend/               無框架靜態前端
tests/                  unit 與 integration tests
```

每個 job 的暫存資料依下列結構保存，並於 TTL 到期後整個刪除：

```text
job_id/
  source/               原始音訊
  analysis/
    mono-22050.wav       可供 key、beat、melody 重用
    stems/
      vocals.wav
      accompaniment.wav
      metadata.json
    pitch/
      vocal_pitch.json
    melody/
      vocals_rmvpe.json
      vocals_rmvpe.mid
    melody.json          目前最佳版本的相容輸出
    melody.mid
  output/                轉調 MP3；最多保留最近兩個版本
  thumbnail.jpg
```

目前採「模組化單體」處理下載、key analysis、beat tracking、RMVPE melody preview、transpose 與 artifact 管理。主 FastAPI 環境保持輕量；Demucs 與 RMVPE 只在 WSL host-native GPU 模式下，透過獨立 GPU venv 的 subprocess 執行。前端仍只連 FastAPI，模型輸出由 FastAPI 正規化並套用既有認證與 job ownership。

## 環境需求

- Python 3.12
- FFmpeg 與 ffprobe
- Rubber Band CLI 3.x 或 4.x
- 需要私人遠端存取時使用 Tailscale

## 本機開發

這個模式讓前端、FastAPI 與本機音訊工具在同一台開發機執行，適合日常開發與功能驗證。Mac 可用 Homebrew；WSL 可用 apt/conda 等既有方式安裝 FFmpeg 與 Rubber Band。

### 1. 安裝系統工具

Mac：

```bash
brew install ffmpeg rubberband
```

WSL / Linux 請確認下列工具可用：

```bash
ffmpeg -version
ffprobe -version
rubberband --version
```

### 2. 建立主 FastAPI 環境

Mac 可使用標準 `venv`：

```bash
mkdir -p ~/venvs
python3.12 -m venv ~/venvs/yt2mp3
source ~/venvs/yt2mp3/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

WSL 可使用目前的 Conda 主環境，例如 `yt2mp3`。不論採用 venv 或 Conda，主環境都不應安裝 Demucs、PyTorch、ONNX Runtime GPU、RMVPE 或 CUDA wheels；GPU runtime 請放在獨立 venv，見 [`docs/gpu.md`](docs/gpu.md)。

### 3. 啟動 FastAPI

開發模式預設不要求密碼：

```bash
uvicorn app.main:app --reload
```

API 位址為 `http://127.0.0.1:8000`，健康檢查為：

```bash
curl http://127.0.0.1:8000/health
```

### 4. 啟動靜態前端

開另一個 Terminal：

```bash
cd frontend
python -m http.server 5500
```

開啟 `http://127.0.0.1:5500`。請勿直接以 `file://` 開啟 `index.html`，否則瀏覽器的 API 與 CORS 行為會不同。

分析歌曲完成後，可在 Step 2 點擊「產生人聲／伴奏」，後端會依序執行 Demucs 與 RMVPE。完成後可在頁面查看 RMVPE 旋律草稿，並下載人聲 MP3、伴奏 MP3 與 MIDI。拍號提示目前由後端以 `analysis/mono-22050.wav` 做保守自動判定，信心不足時會回退為 `none`。

若要在本機模擬正式環境，複製 `.env.example` 為 `.env`，將 `APP_ENV` 改為 `production`，設定 `APP_PASSWORD`、`TOKEN_SECRET` 與正確的 `CORS_ALLOWED_ORIGINS` 後重啟 API。

## Render 前端 + WSL 地端後端

這是目前建議的私人正式部署方式：Render 只託管靜態檔案；YouTube 下載、分析、轉調與可選 GPU 分析都留在 Windows / WSL 地端，API 僅透過 Tailscale 提供給同一 tailnet 裝置。

```text
Render Static Site
        │ 瀏覽器從 tailnet 裝置發出 HTTPS 請求
        ▼
Windows Tailscale Serve
        │ localhost:8001
        ▼
WSL FastAPI systemd / host process
```

### 1. 建立後端設定

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

至少設定：

```dotenv
APP_ENV=production
APP_PASSWORD=請替換為強密碼
TOKEN_SECRET=請填入剛產生的隨機字串
CORS_ALLOWED_ORIGINS=https://你的-render-網站.onrender.com
```

`CORS_ALLOWED_ORIGINS` 必須是完整 origin，不可含路徑、結尾斜線或 `*`。

### 2. 啟動後端

正式後端建議直接在 WSL host 的主 FastAPI 環境執行，讓 Demucs / RMVPE 可透過獨立 GPU venv subprocess 啟動。主 FastAPI 環境不要安裝 GPU 套件。

```bash
/home/startech/miniconda3/envs/yt2mp3/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
curl http://127.0.0.1:8001/health
```

也可以用 repository 內的 host 啟動腳本：

```bash
./start_backend.sh
```

若要常駐，建議建立 systemd user service 或由 WSL 啟動流程呼叫 `start_backend.sh`。GPU 功能請用 `DEMUCS_PYTHON` / `RMVPE_PYTHON` 指向獨立 GPU venv，詳見 [`docs/gpu.md`](docs/gpu.md)。

### 3. 透過 Windows Tailscale Serve 暴露私人 API

在 Windows PowerShell 確認 Tailscale 已登入後執行：

```powershell
tailscale serve --bg 8001
tailscale serve status
```

將 `tailscale serve status` 顯示的 HTTPS 網址作為 Render 的 `BACKEND_API_URL`，不要加結尾斜線。若 Windows 無法連到 `http://127.0.0.1:8001`，先檢查 WSL localhost forwarding 與 FastAPI 是否仍在 WSL host 上執行。

本專案預設不使用 `tailscale funnel`。Funnel 會讓 API 對公網開放，必須先重新評估認證、限流、頻寬與濫用風險。

### 4. 建立 Render 靜態網站

1. 將 repository push 到 Git provider。
2. 在 Render 以 `render.yaml` 建立 Blueprint。
3. 將 `BACKEND_API_URL` 設為上一節取得的 Tailscale Serve HTTPS 網址。
4. 確認後端 `.env` 的 `CORS_ALLOWED_ORIGINS` 是 Render 網站 origin，重啟後端。
5. 使用已登入同一 tailnet 的裝置開啟 Render 網站。

建置腳本只會將公開的 API 位址寫入 `frontend/config.js`，不會把密碼、token secret 或其他後端機密放進靜態網站。

## Host GPU 模式

Host GPU 模式用於 WSL / NVIDIA 環境。主 FastAPI 環境維持輕量，不安裝或 import Demucs、PyTorch、ONNX Runtime GPU、RMVPE 或 CUDA；GPU runtime 安裝在獨立 venv，並透過 subprocess 呼叫。

常用設定：

```dotenv
STEM_SEPARATION_ENABLED=true
STEM_SEPARATION_BACKEND=auto
STEM_SEPARATION_DEVICE=cuda
DEMUCS_PYTHON=/home/startech/venvs/yt2mp3-gpu/bin/python
DEMUCS_MODEL=htdemucs
DEMUCS_TIMEOUT_SECONDS=900
DEMUCS_CLEAN_ENV=true
RMVPE_PYTHON=/home/startech/venvs/yt2mp3-gpu/bin/python
RMVPE_TIMEOUT_SECONDS=300
RMVPE_VOICED_CONFIDENCE_THRESHOLD=0.03
ALLOW_CPU_HEAVY_MODE=false
```

正式 vocal pitch 由 melody pipeline 在選用 vocals source 時觸發；成功時輸出 `analysis/pitch/vocal_pitch.json`。旋律草稿採 RMVPE-only：若 RMVPE、ONNX Runtime、CUDAExecutionProvider 或 vocals stem 不可用，不會產生假的 `vocal_pitch.json` 或 melody artifacts。

更多 GPU venv、subprocess env、CUDA library isolation、已驗證版本與 smoke test 細節，請見 [`docs/gpu.md`](docs/gpu.md)。

## 認證與檔案下載

正式環境中，`POST /api/auth/login` 會以共用密碼交換短效 Bearer token。瀏覽器將 token 放在 `sessionStorage`，關閉分頁後即移除。每個 token 只能存取自己建立的 jobs。

縮圖、MP3 與分析 artifacts 都應透過帶有 `Authorization` header 的請求取得，再由瀏覽器建立 object URL；不應提供公開下載 route。使用 reverse proxy 時，不應信任任意來源提供的 forwarded-IP headers。

## 重要設定

完整範例位於 `.env.example`。

### 基本安全

- `APP_PASSWORD`：正式環境必填的共用登入密碼
- `TOKEN_SECRET`：至少 32 個非預設字元；正式環境必填
- `CORS_ALLOWED_ORIGINS`：允許的靜態前端 origins，以逗號分隔
- `ACCESS_TOKEN_TTL_MINUTES`：token 有效時間，預設 60 分鐘

### Job、來源與暫存

- `JOB_TTL_MINUTES`：job 與暫存檔保存時間，預設 60 分鐘
- `MAX_QUEUE_SIZE`：可等待的背景工作數
- `MAX_CONCURRENT_JOBS`：預留的並行限制；目前單一 worker 仍依序執行
- `MAX_VIDEO_DURATION_SECONDS`／`MAX_SOURCE_MB`：來源限制
- `WORK_ROOT`：暫存工作根目錄
- `YTDLP_COOKIES_FILE`：選用的 yt-dlp cookies 檔案路徑

### Host GPU

- `STEM_SEPARATION_ENABLED`：是否啟用 host stem separation
- `DEMUCS_PYTHON`：獨立 host GPU venv 的 Python 路徑
- `DEMUCS_CLEAN_ENV`：legacy safety flag；啟用 Demucs 時必須維持 `true`，subprocess env 一律由共用 helper 清理
- `RMVPE_PYTHON`：RMVPE／ONNX Runtime GPU venv 的 Python 路徑
- `RMVPE_TIMEOUT_SECONDS`：RMVPE vocal pitch subprocess timeout
- `RMVPE_VOICED_CONFIDENCE_THRESHOLD`：RMVPE confidence 判定 voiced frame 的門檻，預設 `0.03`

## 測試

一般測試驗證主 FastAPI 環境、API、pipeline、artifact 與非 GPU 邏輯：

```bash
ruff check app tests
ruff format --check app tests
pytest -q
```

整合測試需要 PATH 中有 FFmpeg、ffprobe 與 Rubber Band。`pytest -q` 不會把 GPU 套件安裝到主環境，也不應直接 import Demucs、PyTorch、ONNX Runtime GPU 或 RMVPE。

快速檢查部署：

```bash
bash scripts/smoke_test.sh
```

若要依畫面主要步驟分段測試：

```bash
python scripts/run_step1_tests.py
python scripts/run_step2_tests.py
```

需要較完整的錯誤內容時，可把 pytest 參數直接接在後方：

```bash
python scripts/run_step2_tests.py -vv --tb=long
```

GPU runtime 需另外驗證，請先設定 `DEMUCS_PYTHON` / `RMVPE_PYTHON` 指向獨立 GPU venv，再執行：

```bash
python scripts/smoke_demucs.py /path/to/short_mix.wav
python scripts/smoke_rmvpe.py /path/to/analysis/stems/vocals.wav
```

## 發展規劃

### Phase 1：基礎轉調工具（已完成）

- YouTube 音訊取得、全局調性分析、升降 Key MP3
- 模組化 job、artifact、pipeline 與 queue 邊界
- 保留標準化分析 WAV，讓後續分析重用

### Phase 2：練唱分析基礎（已完成）

- RMVPE melody preview：Demucs vocals、RMVPE vocal pitch、Melody JSON 與 MIDI
- Host GPU stem separation：Demucs 產生 `vocals.wav`、`accompaniment.wav` 與 `metadata.json`
- RMVPE vocal pitch backend：從 vocals stem 產生 `analysis/pitch/vocal_pitch.json`
- melody 目前採 vocals/RMVPE-only；mix melody fallback 已移除
- 旋律草稿採 RMVPE-only，不做 CPU pitch fallback
- Demucs 或 RMVPE 不可用時，不產生假的 stems 或假的 pitch artifacts

### Phase 3：練唱旋律整理（下一階段）

- 從 vocal pitch curve 產生 raw、clean、simplified note candidates
- 改善顫音、滑音、裝飾音與低可信度片段

### Phase 4：練唱操作體驗

- 原曲、人聲、伴奏與升降 Key 版本切換
- 伴奏播放、下載、逐句播放與 A/B loop

### Phase 5：人工修正、合音與進階輸出

- 保存 raw、clean、simplified melody 與 user edits
- 產生及比對疑似二部合音
- 評估 MusicXML、PDF、MuseScore export
- 資料品質足夠後，再評估訓練旋律清理或合音生成模型

## 已知限制

- 只支援一般 watch、`youtu.be`、mobile watch 與 Shorts 單一公開影片網址。
- 不支援播放清單、直播、頻道或任意網站。
- YouTube 可能回傳 403、429 或要求 bot verification。
- 全局調性可能混淆關係大小調，也不適合歌曲中途轉調的情況。
- 自動拍號判定採保守啟發式，可能判錯或在信心不足時回退為不分小節。
- Demucs 可能無法完全分離主唱與和聲，也可能產生音訊 artifact。
- Vocal pitch backend 採 RMVPE-only；RMVPE、ONNX Runtime、CUDA 或 vocals stem 不可用時，不產生假的 `analysis/pitch/vocal_pitch.json` 或 melody artifacts。
- 目前下載的 MIDI 可能無法由 QuickTime Player 開啟；可先使用 MuseScore、DAW 或其他 MIDI player 檢視。
- Host GPU 模式需要 WSL host-native FastAPI 與獨立 GPU venv；本專案不維護本機 Docker 後端部署路線。
- Render 只提供前端；音訊流量與運算仍使用地端電腦資源。
- 地端電腦必須保持開機，遠端裝置也必須連入同一 tailnet。

## 依賴更新與規格

依賴檔維持簡單結構：

- `requirements.txt`：主 FastAPI runtime 需要的套件。
- `requirements-dev.txt`：本機開發與測試工具，例如 `pytest`、`pytest-asyncio`、`pytest-cov`、`ruff` 與 `httpx`。
- `requirements-gpu.txt`：host GPU venv 使用，不安裝進 production image。

目前不使用 `requirements.in`、`requirements-dev.in` 或 pip-tools。GPU 依賴升級時，先在 `/home/startech/venvs/yt2mp3-gpu` 驗證 Demucs 與 RMVPE smoke test，再同步更新 `requirements-gpu.txt` 與 `docs/gpu.md`。

依賴或系統套件變更時，請同步檢查 `THIRD_PARTY_NOTICES.md`。

更多工程文件放在 [`docs/`](docs/)。[`docs/index.md`](docs/index.md) 說明文件結構與一致性規則；[`docs/gpu.md`](docs/gpu.md) 記錄 Host GPU runtime 細節；[`docs/yt2mp3-spec.md`](docs/yt2mp3-spec.md) 保留為 MVP 的產品需求與驗收基準。
