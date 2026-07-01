# yt2mp3

yt2mp3 是私人使用、行動裝置友善的練唱素材生成工具。使用者貼上有權處理的 YouTube 單一影片網址後，系統會取得音訊、分析全局調性、產生升降 Key MP3，並可在 WSL host GPU 模式下產生人聲／伴奏、frame-level pitch、adaptive melody fusion 旋律草稿、MIDI 與簡譜草稿 artifacts。

本專案用於私人練唱輔助，不定位為正式樂譜或準確扒譜工具。請只處理自己擁有或已獲授權使用的內容；本專案不繞過 DRM、私人影片、地區限制或 YouTube 的存取控制。

## 目前功能

- YouTube 單一影片網址驗證、音訊下載與縮圖取得
- Krumhansl-Schmuckler profile 全局調性分析
- 原調、可信度、候選調性與升降 2-3 個半音選項
- Rubber Band 轉調，維持原速度
- 128 / 192 / 256 kbps MP3 輸出
- Bearer token、job ownership、登入限流與嚴格 CORS
- 暫存 job queue、取消、TTL 清除與最近輸出快取
- Render 靜態前端 + WSL / Tailscale 地端 API
- Host GPU 模式下的 Demucs stems、RMVPE cache、四 backend pitch CSV、adaptive melody fusion、postprocessed melody tracks、Melody JSON、MIDI 與新版簡譜草稿 artifacts

## 架構

前端與後端透過 HTTP API 分離，但目前仍放在同一個 repository，方便共同開發與版本管理。

```text
Render 或本機靜態前端
        |
        | HTTPS / JSON / polling
        v
FastAPI API（WSL / host-native）
  |- Job manager：job 生命週期、擁有權、TTL
  |- Task queue：背景工作排程邊界
  |- Pipelines：analyze、stems、melody fusion、transpose
  |- Model backends：Demucs / pitch subprocess adapters
  |- Melody fusion：只讀標準 CSV，不 import GPU runtime
  `- Artifacts：來源、分析產物與輸出檔案
```

主要目錄：

```text
app/                     FastAPI routes、models、services 與 pipelines
frontend/                無框架靜態前端
scripts/                 active smoke、debug、backend support scripts
tests/                   unit / integration tests
docs/                    GPU、fusion、索引與 archive 文件
docs/archive/            歷史研發紀錄；不代表目前 active runtime
```

每個 job 的暫存 artifact 會在 TTL 到期後整個刪除：

```text
job_id/
  source/
  analysis/
    mono-22050.wav
    stems/
      vocals.wav
      accompaniment.wav
      metadata.json
    pitch/
      vocal_pitch.json
    melody/
      vocals_adaptive_fusion.json
      vocals_adaptive_fusion.mid
      fusion/
        inputs/
          rmvpe.csv
          torchcrepe.csv
          fcpe.csv
          pesto.csv
        fusion.csv
        fusion.json
        comparison.csv
        postprocessed.csv
        postprocessed.json
        postprocess_diagnostics.json
        diagnostics.json
    melody.json
    melody.mid
    rhythm/
      beat_grid.json
      vocal_onsets.csv
      notes_draft.json
      notes_draft.csv
      numbered_notation.json
      jianpu_draft.txt
      rhythm_diagnostics.json
  output/
  thumbnail.jpg
```

主 FastAPI 環境保持輕量，不安裝也不 import Demucs、PyTorch、ONNX Runtime GPU、RMVPE 或 CUDA packages。GPU runtime 放在獨立 venv，透過 subprocess 執行。

## 快速開始

需要 Python 3.12、FFmpeg / ffprobe、Rubber Band。

Mac:

```bash
brew install ffmpeg rubberband
```

WSL / Linux:

```bash
ffmpeg -version
ffprobe -version
rubberband --version
```

Mac 可用 venv：

```bash
mkdir -p ~/venvs
python3.12 -m venv ~/venvs/yt2mp3
source ~/venvs/yt2mp3/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

WSL 可使用既有 Conda 主環境，例如 `/home/startech/miniconda3/envs/yt2mp3`。主環境不要安裝 GPU packages；GPU venv 請看 [docs/gpu.md](docs/gpu.md)。

啟動本機 API：

```bash
uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health
```

repo 內的啟動腳本預設使用本機 API port `8000`：

```bash
./start_backend.sh
curl http://127.0.0.1:8000/health
```

啟動本機前端：

```bash
cd frontend
python -m http.server 5500
```

開啟 `http://127.0.0.1:5500`。不要用 `file://` 開啟 `index.html`，避免 API 與 CORS 行為不同。

## Render 前端 + WSL 地端後端

目前建議的私人正式部署方式是：Render 只提供靜態前端，下載、分析、轉調與 GPU 工作都在地端 WSL 後端執行。

```text
Render Static Site
        |
        | browser HTTPS request
        v
Windows Tailscale Serve
        |
        | http://127.0.0.1:8000
        v
WSL FastAPI backend
```

後端 `.env`：

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

至少設定：

```dotenv
APP_ENV=production
APP_PASSWORD=請替換為強密碼
TOKEN_SECRET=請填入隨機字串
CORS_ALLOWED_ORIGINS=https://你的-render-網站.onrender.com
```

`CORS_ALLOWED_ORIGINS` 必須是完整 origin，不可含路徑、結尾斜線或 `*`。完整範例見 [.env.example](.env.example)。

啟動後端：

```bash
./start_backend.sh
curl http://127.0.0.1:8000/health
```

Windows PowerShell 設定 Tailscale Serve：

```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8000
tailscale serve status
```

將 `tailscale serve status` 顯示的 HTTPS URL 設為 Render 的 `BACKEND_API_URL`，不要加結尾斜線。請確認 Tailscale Serve 的 `/` proxy 到 yt2mp3 的 `127.0.0.1:8000`。

本專案預設不使用 `tailscale funnel`。Funnel 會讓 API 對公網開放，必須先重新評估認證、限流、頻寬與濫用風險。

## Host GPU 與 Melody Fusion

Host GPU 模式用於 WSL / NVIDIA 環境。主 FastAPI 環境維持輕量，Demucs 和 pitch backend 都透過 GPU venv subprocess 執行。

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

Melody fusion 目前契約：

- 四個 pitch backend 統一輸出 CSV：`rmvpe`、`torchcrepe`、`fcpe`、`pesto`
- fusion 模組只讀 CSV，不 import GPU runtime
- 至少 2 個 backend 成功才會 fuse
- fusion 失敗時不 fallback 成 RMVPE-only 成功，也不覆蓋既有 melody artifacts
- 成功時會 best-effort 產生 `comparison.csv`、`postprocessed.csv`、`postprocessed.json` 與 `postprocess_diagnostics.json`
- postprocess 失敗時只在 diagnostics 記錄 warning，不會讓 melody job 失敗，也不會覆蓋已產生的 melody artifacts
- 成功時輸出前端相容的 `analysis/melody.json` 與 `analysis/melody.mid`
- RMVPE legacy cache `analysis/pitch/vocal_pitch.json` 仍保留

舊版 RMVPE-only `MelodyPipeline` 已移除；目前正式 melody job 使用 `MelodyFusionPipeline` 主線。RMVPE 仍作為 fusion backend 保留，透過 `scripts/run_rmvpe_pitch.py` 產生 RMVPE pitch JSON，再轉為 fusion CSV rows 使用。

目前主旋律 / 簡譜主線採 `hybrid_postprocessed`：RMVPE 為主，Fusion 只在 RMVPE 缺值且其他模型支持時補洞。詳細 V4.2 實驗紀錄請見 [docs/archive/melody-v4.2.md](docs/archive/melody-v4.2.md)。

指定 WAV 的本機 debug / 差異比對：

```bash
python scripts/run_melody_fusion_debug.py /mnt/d/vocals-1.wav \
  --out-dir /tmp/yt2mp3-fusion-debug
```

輸出包含 `inputs/*.csv`、`fusion.csv`、`fusion.json`、`comparison.csv`、`postprocessed.csv`、`postprocessed.json`、`postprocess_diagnostics.json` 與 `diagnostics.json`。

更多細節：

- [docs/gpu.md](docs/gpu.md)：GPU venv、環境隔離、smoke tests、疑難排查
- [docs/melody_fusion.md](docs/melody_fusion.md)：CSV schema、fusion artifacts、CLI、debug output

## Rhythm Pipeline 與簡譜草稿

Rhythm Pipeline 會把既有音訊與 pitch artifacts 串成節奏檢查資料。當 melody fusion 成功或命中已快取的 melody variant 時，後端會 best-effort 產生新版簡譜草稿 artifacts；失敗時只記錄 warning，不會讓 melody job 失敗，也不會覆蓋 `melody.json` / `melody.mid`。

Rhythm pipeline 會使用 postprocessed melody timeline、beat grid 與 vocal onset 產生 `notes_draft`、`numbered_notation.json` 與 `jianpu_draft.txt`。詳細 Phase 2.2.3 segmentation 研發紀錄請見 [docs/archive/rhythm-phase-2.2.3.md](docs/archive/rhythm-phase-2.2.3.md)。

目前 rhythm artifacts：

- `analysis/rhythm/beat_grid.json`：由 accompaniment 優先、mono audio fallback 產生的 beat / tempo / meter 初版結果
- `analysis/rhythm/vocal_onsets.csv`：由 vocals 優先、mono audio fallback 產生的 vocal onset 候選切點
- `analysis/rhythm/notes_draft.json`：由 postprocessed pitch timeline、beat grid、vocal onsets 組成的草稿音符 JSON
- `analysis/rhythm/notes_draft.csv`：方便人工檢查的草稿音符表格
- `analysis/rhythm/numbered_notation.json`：由 notes draft、調性與大小調轉出的新版簡譜結構化資料
- `analysis/rhythm/jianpu_draft.txt`：前端可預覽與下載的文字簡譜草稿
- `analysis/rhythm/rhythm_diagnostics.json`：pipeline backend、來源、fallback、warning 與 note stats 摘要

API response 會在 `notation_artifacts` 回傳可用狀態、下載 URL 與 warnings。新版 artifacts 可透過以下 route 下載：

```text
GET /api/jobs/{job_id}/notation/download/numbered-notation-json
GET /api/jobs/{job_id}/notation/download/jianpu-draft-txt
GET /api/jobs/{job_id}/notation/download/notes-draft-json
GET /api/jobs/{job_id}/notation/download/notes-draft-csv
GET /api/jobs/{job_id}/notation/download/rhythm-diagnostics-json
```

手動從 `notes_draft.json` 產生簡譜草稿：

```bash
python scripts/build_jianpu_draft.py --job-dir <JOB_DIR> --key C --mode major
```

手動匯出 rhythm debug 檔：

```bash
python scripts/export_rhythm_debug.py --job-dir <JOB_DIR> --out /tmp/rhythm-debug
```

## 認證與安全

正式環境中，`POST /api/auth/login` 會以共用密碼交換短效 Bearer token。瀏覽器將 token 放在 `sessionStorage`，關閉分頁後移除。每個 token 只能存取自己建立的 jobs。

縮圖、MP3 與分析 artifacts 都透過帶有 `Authorization` header 的請求取得，再由瀏覽器建立 object URL；不提供公開下載 route。使用 reverse proxy 時，不應信任任意來源提供的 forwarded-IP headers。

重要設定：

- `APP_PASSWORD`：正式環境必填的共用登入密碼
- `TOKEN_SECRET`：至少 32 個非預設字元
- `CORS_ALLOWED_ORIGINS`：允許的前端 origins
- `ACCESS_TOKEN_TTL_MINUTES`：token 有效時間
- `JOB_TTL_MINUTES`：job 與暫存檔保存時間
- `MAX_QUEUE_SIZE`：可等待的背景工作數
- `WORK_ROOT`：暫存工作根目錄
- `YTDLP_COOKIES_FILE`：選用 yt-dlp cookies 檔案路徑

## 測試與 Smoke

主環境測試：

```bash
ruff check app tests scripts
ruff format --check app tests scripts
pytest -q
```

`docs/archive/scripts/` 為封存研發腳本，不屬於主 runtime 驗收；若需要維護 archive scripts，可額外執行 `ruff check docs/archive/scripts`。

在 Codex 或非互動 shell 驗收時，建議使用：

```bash
./scripts/check_codex.sh
```

此腳本會固定 Python interpreter、`NUMBA_CACHE_DIR` 與 pytest timeout，避免 conda/session 環境差異造成誤判。

### Python 版本與測試警告

本專案目前以 Python 3.12 為目標版本，`pyproject.toml` 限制為 `>=3.12,<3.13`。

若 `pytest` 在 Python 3.12 顯示來自第三方套件 `audioread` 的 `aifc`、`audioop`、`sunau` deprecation warnings，這些是 Python 3.13 相容性提醒，不影響目前 Python 3.12 測試結果。

快速部署檢查：

```bash
bash scripts/smoke_test.sh
```

依畫面步驟分段測試：

```bash
python scripts/run_step1_tests.py
python scripts/run_step2_tests.py
```

GPU smoke / debug：

```bash
python scripts/smoke_demucs.py /path/to/short_mix.wav
python scripts/smoke_rmvpe.py /path/to/analysis/stems/vocals.wav
python scripts/smoke_fcpe.py /path/to/vocals.wav /tmp/fcpe.csv
python scripts/smoke_pesto.py /path/to/vocals.wav /tmp/pesto.csv
python scripts/smoke_torchcrepe.py /path/to/vocals.wav /tmp/torchcrepe.csv
python scripts/run_melody_fusion_debug.py /path/to/vocals.wav --out-dir /tmp/yt2mp3-fusion-debug
python scripts/export_rhythm_debug.py --job-dir /path/to/job --out /tmp/rhythm-debug
python scripts/build_jianpu_draft.py --job-dir /path/to/job --key C --mode major
python scripts/compare_notes_draft.py --help
```

已封存的研發腳本僅供查閱或必要時手動復現舊實驗；不屬於主流程驗收。相關細節請見 `docs/archive/`。

GPU smoke scripts 應在 GPU venv 或透過正確的 GPU Python 執行；不要把 GPU dependencies 安裝到主 FastAPI runtime。

## 已知限制

- 只支援一般 watch、`youtu.be`、mobile watch 與 Shorts 單一公開影片網址
- 不支援播放清單、直播、頻道或任意網站
- YouTube 可能回傳 403、429 或要求 bot verification
- 全局調性可能混淆關係大小調，也不適合歌曲中途轉調
- 自動拍號判定採保守啟發式，信心不足時回退為 `none`
- Demucs 可能無法完全分離主唱與和聲，也可能產生 artifact
- Adaptive fusion 無法保證在和聲很重時選到真正主旋律
- 新版簡譜草稿是 best-effort 自動輸出，拍點、切分、八度與臨時記號都可能需要人工校正
- 少於兩個 pitch backend 成功時，不產生新的 fusion-based melody artifacts
- MIDI 可先用 MuseScore、DAW 或其他 MIDI player 檢視
- Render 只提供前端；音訊流量與運算仍使用地端電腦資源
- 地端電腦需保持開機，遠端裝置也需可連到對應 Tailscale / Serve endpoint

## 文件索引

- [docs/index.md](docs/index.md)：文件一致性規則與 archive 索引
- [docs/gpu.md](docs/gpu.md)：Host GPU runtime 詳細工程文件
- [docs/melody_fusion.md](docs/melody_fusion.md)：Adaptive melody fusion 契約
- [docs/archive/yt2mp3-spec-v1.1.md](docs/archive/yt2mp3-spec-v1.1.md)：歷史 MVP 產品與技術規格
- [docs/archive/melody-v4.2.md](docs/archive/melody-v4.2.md)：Melody V4.2 實驗紀錄
- [docs/archive/rhythm-phase-2.2.3.md](docs/archive/rhythm-phase-2.2.3.md)：Rhythm Phase 2.2.3 segmentation 研發紀錄

依賴檔：

- `requirements.txt`：主 FastAPI runtime
- `requirements-dev.txt`：本機開發與測試工具
- `requirements-gpu.txt`：host GPU venv；不要安裝進主 runtime
- `requirements-research.txt`：封存研發腳本需要的額外套件

依賴或系統套件變更時，請同步檢查 `THIRD_PARTY_NOTICES.md`。
