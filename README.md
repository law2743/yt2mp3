# yt2mp3

yt2mp3 是一套私人使用、行動裝置友善的練唱素材生成工具。使用者貼上有權處理的 YouTube 影片網址後，系統會分析歌曲可能的全局調性，提供升降 Key MP3，並逐步加入人聲／伴奏分離、人聲音高追蹤、練唱旋律草稿與伴奏練唱功能。

目前穩定功能仍以 YouTube 音訊取得、全局調性分析、升降 Key 與 MP3 輸出為主；CPU-only 主旋律簡譜僅作為 preview／fallback，不定位為正式樂譜或準確扒譜結果。

目前版本使用 `yt-dlp`、FFmpeg、librosa 與 Rubber Band，不使用生成式 AI。請僅處理自己擁有或已獲授權使用的內容；本專案不繞過 DRM、私人影片、地區限制或 YouTube 的存取控制。

## 目前功能

- 驗證 YouTube 單一影片網址並取得音訊與縮圖
- 使用 Krumhansl–Schmuckler profile 分析全局調性
- 顯示原調、可信度、候選調性及前後 2～3 個半音選項
- 使用 Rubber Band 轉調，維持原本速度
- 輸出 128、192 或 256 kbps MP3
- 使用 beat tracking 與 pYIN 產生 CPU-only 旋律預覽、Melody JSON 與 MIDI
- 可由 WSL host-native FastAPI 呼叫獨立 GPU venv 的 Demucs，產生人聲與伴奏 WAV
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
  ├─ Analyzers：可替換的音樂分析器
  └─ Job artifacts：來源、分析產物與輸出檔案
```

主要目錄：

```text
app/
  api/                  FastAPI routes 與認證
  models/
    api.py              API 請求模型
    job.py              job 狀態與回應模型
    music.py            調性及未來音樂分析模型
  services/
    artifacts.py        job 暫存目錄與產物路徑
    job_manager.py      job registry、生命週期與 worker
    task_queue.py       可替換的工作佇列邊界
    pipelines/          analyze、transpose 流程
    audio.py            FFmpeg 與 Rubber Band 音訊處理
    key_analyzer.py     librosa 調性分析
    youtube.py          yt-dlp adapter
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
    melody/
      mix_pyin.json
      mix_pyin.mid
      vocals_pyin.json
      vocals_pyin.mid
    melody.json          目前最佳版本的相容輸出
    melody.mid
  output/                轉調 MP3；最多保留最近兩個版本
  thumbnail.jpg
```

目前採「模組化單體」處理下載、key analysis、beat tracking、CPU-only melody preview、transpose 與 artifact 管理。Demucs 透過 adapter 呼叫獨立 GPU venv；前端仍只連 FastAPI，模型輸出由 FastAPI 正規化並套用既有認證與 job ownership。

## 環境需求

- Python 3.12
- FFmpeg 與 ffprobe
- Rubber Band CLI 3.x 或 4.x
- 需要私人遠端存取時使用 Tailscale
- Docker Desktop 或 Docker Engine（部署方式二需要）

## 部署方式一：全 Mac 本機測試

這個模式讓前端、FastAPI 與所有音訊工具都在同一台 Mac 執行，不需要 Docker，也不需要 Render 或 Tailscale，適合日常開發與功能驗證。

### 1. 安裝系統工具

使用 Homebrew：

```bash
brew install ffmpeg rubberband
```

使用 Python 3.12 的標準 `venv` 建立名為 `yt2mp3` 的虛擬環境。以下將環境放在家目錄的 `venvs` 中，避免和 repository 目錄同名：

```bash
mkdir -p ~/venvs
python3.12 -m venv ~/venvs/yt2mp3
source ~/venvs/yt2mp3/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

確認工具可用：

```bash
ffmpeg -version
ffprobe -version
rubberband --version
```

### 2. 啟動 FastAPI

開發模式預設不要求密碼：

```bash
source ~/venvs/yt2mp3/bin/activate
uvicorn app.main:app --reload
```

API 位址為 `http://127.0.0.1:8000`，健康檢查為：

```bash
curl http://127.0.0.1:8000/health
```

### 3. 啟動靜態前端

開另一個 Terminal：

```bash
cd frontend
python -m http.server 5500
```

開啟 `http://127.0.0.1:5500`。請勿直接以 `file://` 開啟 `index.html`，否則瀏覽器的 API 與 CORS 行為會不同。

分析歌曲完成後，可在轉調選項下方選擇拍號提示並點擊「產生主旋律簡譜草稿」。支援 `auto`、`none`、`4/4`、`3/4` 與 `6/8`；`auto` 無法可靠判定時不會強制加入小節線。完成後可在頁面查看簡譜草稿，並下載 Melody JSON 或 MIDI。

主旋律功能直接分析完整混音，沒有做人聲分離，因此可能包含伴奏、和聲或錯誤音符。輸出的秒數與 MIDI 音高是主要資料；BPM、beat、小節及拍號屬估計資訊。

若要在 Mac 本機模擬正式環境，複製 `.env.example` 為 `.env`，將 `APP_ENV` 改為 `production`，設定 `APP_PASSWORD`、`TOKEN_SECRET` 與正確的 `CORS_ALLOWED_ORIGINS` 後重啟 API。

### 未來接 Mac GPU／加速器

第一階段的 beat、pYIN melody 與規則型 harmony 仍直接在 FastAPI 地端執行。導入需要加速器的模型後，預計提供兩個 adapter：

- `LocalGpuAdapter`：在同一台 Apple Silicon Mac 使用模型支援的 MPS／Metal 能力
- `RemoteGpuAdapter`：將任務送往同一 tailnet 內的獨立 GPU worker

模型是否支援 MPS 必須逐一驗證；不會假設所有 CUDA 模型都能直接在 Mac 執行。

## 部署方式二：Render 前端 + WSL 地端後端

這是目前建議的私人正式部署方式：Render 只託管靜態檔案；YouTube 下載、分析與轉調都留在 Windows 的 WSL／Docker 中，API 僅透過 Tailscale 提供給同一 tailnet 裝置。

```text
Render Static Site
        │ 瀏覽器從 tailnet 裝置發出 HTTPS 請求
        ▼
Windows Tailscale Serve
        │ localhost:8001
        ▼
WSL Docker Compose / FastAPI
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

### 2. 在 WSL 啟動後端

```bash
docker compose up -d --build
curl http://127.0.0.1:8001/health
```

Compose 只將容器的 8000 port 發布到 WSL／Windows loopback 的 `127.0.0.1:8001`，容器會在重啟後自動恢復。job registry 在記憶體中，容器重建或後端重啟後，既有 job 會失效。

### 3. 透過 Windows Tailscale Serve 暴露私人 API

在 Windows PowerShell 確認 Tailscale 已登入後執行：

```powershell
tailscale serve --bg 8001
tailscale serve status
```

將 `tailscale serve status` 顯示的 HTTPS 網址作為 Render 的 `BACKEND_API_URL`，不要加結尾斜線。若 Windows 無法連到 `http://127.0.0.1:8001`，先檢查 Docker Desktop 的 WSL integration 與 Windows／WSL localhost forwarding。

本專案預設不使用 `tailscale funnel`。Funnel 會讓 API 對公網開放，必須先重新評估認證、限流、頻寬與濫用風險。

### 4. 建立 Render 靜態網站

1. 將 repository push 到 Git provider。
2. 在 Render 以 `render.yaml` 建立 Blueprint。
3. 將 `BACKEND_API_URL` 設為上一節取得的 Tailscale Serve HTTPS 網址。
4. 確認後端 `.env` 的 `CORS_ALLOWED_ORIGINS` 是 Render 網站 origin，重啟後端。
5. 使用已登入同一 tailnet 的裝置開啟 Render 網站。

建置腳本只會將公開的 API 位址寫入 `frontend/config.js`，不會把密碼、token secret 或其他後端機密放進靜態網站。

### WSL／NVIDIA GPU（Phase 2B host-native）

Phase 2B 第一版不把 Demucs 包進 Docker image。FastAPI 主環境維持輕量，不安裝或 import Demucs、PyTorch 與 CUDA；host-native FastAPI 只透過 subprocess 呼叫獨立 GPU venv：

```text
FastAPI orchestrator
  ├─ download、key、beat、transpose
  ├─ CPU-only melody preview／fallback
  └─ /home/startech/venvs/yt2mp3-gpu/bin/python -m demucs
```

Docker 部署目前仍只支援 Phase 1 與 Phase 2A；容器不能直接執行 WSL host venv。要使用 Demucs，請在 WSL host 啟動 FastAPI，並設定：

```dotenv
STEM_SEPARATION_ENABLED=true
STEM_SEPARATION_BACKEND=auto
STEM_SEPARATION_DEVICE=cuda
DEMUCS_PYTHON=/home/startech/venvs/yt2mp3-gpu/bin/python
DEMUCS_MODEL=htdemucs
DEMUCS_TIMEOUT_SECONDS=900
DEMUCS_CLEAN_ENV=true
ALLOW_CPU_HEAVY_MODE=false
```

Demucs subprocess 會移除 `LD_LIBRARY_PATH` 與 `PYTHONPATH`，避免誤載其他 conda environment 的 CUDA／cuDNN。Demucs、PyTorch、TorchCodec、模型權重與 cache 均不放入主 requirements 或 repository。

本機 GPU venv 已驗證的組合為 Python 3.12.3、PyTorch／torchaudio 2.11.0+cu128、TorchCodec 0.11.0 CPU wheel、Demucs 4.0.1 與 NVIDIA GeForce RTX 5070。TorchCodec 使用 CPU wheel 只負責輸出音訊；Demucs 推論仍透過 PyTorch 使用 CUDA。

手動 smoke test：

```bash
python scripts/smoke_demucs.py /path/to/short_mix.wav
```

若 Demucs、torch 或 CUDA 不可用，系統不會產生假的 `vocals.wav`／`accompaniment.wav`，只會寫入 fallback metadata，旋律分析繼續使用完整混音。

## 認證與檔案下載

正式環境中，`POST /api/auth/login` 會以共用密碼交換短效 Bearer token。瀏覽器將 token 放在 `sessionStorage`，關閉分頁後即移除。每個 token 只能存取自己建立的 jobs。

縮圖與 MP3 都透過帶有 `Authorization` header 的請求取得，再由瀏覽器建立 object URL；沒有公開下載 route。使用 reverse proxy 時，不應信任任意來源提供的 forwarded-IP headers。

## 重要設定

完整範例位於 `.env.example`：

- `APP_PASSWORD`：正式環境必填的共用登入密碼
- `TOKEN_SECRET`：至少 32 個非預設字元；正式環境必填
- `CORS_ALLOWED_ORIGINS`：允許的靜態前端 origins，以逗號分隔
- `ACCESS_TOKEN_TTL_MINUTES`：token 有效時間，預設 60 分鐘
- `JOB_TTL_MINUTES`：job 與暫存檔保存時間，預設 60 分鐘
- `MAX_QUEUE_SIZE`：可等待的背景工作數
- `MAX_CONCURRENT_JOBS`：預留的並行限制；目前單一 worker 仍依序執行
- `MAX_VIDEO_DURATION_SECONDS`／`MAX_SOURCE_MB`：來源限制
- `WORK_ROOT`：暫存工作根目錄
- `YTDLP_COOKIES_FILE`：選用的 yt-dlp cookies 檔案路徑
- `STEM_SEPARATION_ENABLED`：是否啟用 Phase 2B；Docker 部署應維持 `false`
- `DEMUCS_PYTHON`：獨立 host GPU venv 的 Python 路徑
- `DEMUCS_CLEAN_ENV`：執行 Demucs 前清除 `LD_LIBRARY_PATH`／`PYTHONPATH`

## 測試

```bash
source ~/venvs/yt2mp3/bin/activate
ruff check app tests
ruff format --check app tests
pytest -q
```

整合測試需要 PATH 中有 FFmpeg、ffprobe 與 Rubber Band。快速檢查部署：

```bash
bash scripts/smoke_test.sh
```

若要依畫面主要步驟分段測試，兩支 runner 會逐一執行並輸出每個 test Python 檔案的結果，遇到失敗仍會繼續跑完同一步驟，最後列出摘要：

```bash
python scripts/run_step1_tests.py
python scripts/run_step2_tests.py
```

需要較完整的錯誤內容時，可把 pytest 參數直接接在後方：

```bash
python scripts/run_step2_tests.py -vv --tb=long
```

## 分階段發展規劃

### Phase 1：基礎轉調工具與模組化骨架（已完成）

- 模型、task queue、job artifacts 與 pipelines 分離
- 保留標準化分析 WAV，讓後續分析重用
- 維持既有調性分析與轉調 API 行為
- 新功能採獨立 subtask，避免阻塞既有轉調結果

### Phase 2：練唱分析基礎

#### Phase 2A：CPU-only melody preview／fallback（已完成）

- 使用 librosa 建立 beat／bar grid
- 以 `librosa.pyin` 抽取主旋律候選
- f0 分段、短音清除、相鄰同音合併與節拍量化
- 輸出 `melody.json`、MIDI 與前端簡譜草稿
- 保存 `mix_pyin` 與 `vocals_pyin` 具名版本；`melody.json`／MIDI 保留為相容輸出
- Basic Pitch 僅作為可選 adapter，不寫死在核心流程

#### Phase 2B：Stem separation backend（已完成第一版）

- host-native FastAPI 透過 subprocess 呼叫獨立 Demucs GPU venv
- 正規化輸出 `vocals.wav`、`accompaniment.wav` 與 `metadata.json`
- 使用獨立 stem queue，GPU 工作不阻塞既有分析／轉調 queue
- Demucs 不可用時採 none fallback，不產生 HPSS 或假人聲 artifacts
- melody 支援 `source=mix`、`vocals`、`auto`

#### Phase 2C：Vocal pitch backend

- 導入 RMVPE，pYIN 保留為 CPU fallback
- 從 vocals stem 輸出 `analysis/pitch/vocal_pitch.json`

### Phase 3：練唱旋律整理

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

- 全局調性可能混淆關係大小調，也不適合歌曲中途轉調的情況。
- YouTube 可能回傳 403、429 或要求 bot verification。
- 只接受一般 watch、`youtu.be`、mobile watch 與 Shorts 單一影片網址。
- 不支援播放清單、直播、頻道或任意網站。
- Render 只提供前端；音訊流量與運算仍使用地端電腦資源。
- 地端電腦必須保持開機，遠端裝置也必須連入同一 tailnet。
- pYIN 在完整混音、多聲部、spoken word 或強烈伴奏歌曲上可能無法可靠辨識主旋律。
- Phase 2A 不做自動拍號辨識；`auto` 不確定時回退為不分小節。
- Demucs 可能無法完全分離主唱與和聲，也可能產生音訊 artifact。
- Phase 2B host GPU 模式目前不支援 Docker；Docker 後端應停用 stem separation。

## 依賴更新與規格

修改 `requirements.in` 或 `requirements-dev.in` 後重新產生 lock files：

```bash
pip-compile --output-file requirements.txt requirements.in
pip-compile --output-file requirements-dev.txt requirements-dev.in
```

依賴或系統套件變更時，請同步檢查 `THIRD_PARTY_NOTICES.md`。

[`yt2mp3-spec.md`](yt2mp3-spec.md) 保留為 MVP 的產品需求與驗收基準；目前部署方式與未來發展方向則以本 README 為準。
