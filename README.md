# yt2mp3

yt2mp3 是私人使用、行動裝置友善的練唱素材生成工具。使用者貼上有權處理的 YouTube 單一影片網址後，系統會取得音訊、分析全局調性、產生升降 Key MP3，並可在 WSL host GPU 模式下產生人聲／伴奏、frame-level pitch、adaptive melody fusion 旋律草稿與 MIDI。

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
app/
  api/                    FastAPI routes 與認證
  models/                 API、job、music 資料模型
  services/
    artifacts.py          job artifact 路徑
    job_manager.py        job registry、worker、public response
    pipelines/            analyze、stems、melody fusion、transpose
    melody_fusion/        CSV-based adaptive fusion 內部模組
    model_backends/       Demucs / RMVPE subprocess adapters
    gpu_subprocess_env.py GPU subprocess 環境隔離 helper
frontend/                 無框架靜態前端
scripts/                  smoke、debug、backend scripts
tests/                    unit / integration tests
docs/                     GPU、fusion、歷史 spec 與開發文件
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
      vocal_pitch.json              # legacy RMVPE cache
    melody/
      vocals_adaptive_fusion.json
      vocals_adaptive_fusion.mid
      fusion/
        vocals_mono_16000.wav
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
    melody.json                     # 前端相容輸出
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

### 系統工具

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

### 建立主 FastAPI 環境

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

### 啟動本機 API

開發預設可用 uvicorn：

```bash
uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health
```

repo 內的啟動腳本預設使用本機 API port `8000`：

```bash
./start_backend.sh
curl http://127.0.0.1:8000/health
```

### 啟動本機前端

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

### 後端 `.env`

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

`CORS_ALLOWED_ORIGINS` 必須是完整 origin，不可含路徑、結尾斜線或 `*`。

### 啟動後端

```bash
./start_backend.sh
curl http://127.0.0.1:8000/health
```

若要常駐，可用 systemd user service 或 WSL 啟動流程呼叫 `start_backend.sh`。

### Tailscale Serve

Windows PowerShell:

```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8000
tailscale serve status
```

將 `tailscale serve status` 顯示的 HTTPS URL 設為 Render 的 `BACKEND_API_URL`，不要加結尾斜線。

注意：請確認 Tailscale Serve 的 `/` 真的 proxy 到 yt2mp3 的 `127.0.0.1:8000`。Render 通常不用改；連錯時多半是 Serve target 指錯。

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

指定 WAV 的本機 debug / 差異比對：

```bash
python scripts/run_melody_fusion_debug.py /mnt/d/vocals-1.wav \
  --out-dir /tmp/yt2mp3-fusion-debug
```

輸出包含 `inputs/*.csv`、`fusion.csv`、`fusion.json`、`comparison.csv`、`postprocessed.csv`、`postprocessed.json`、`postprocess_diagnostics.json` 與 `diagnostics.json`。

### Melody V4.2 實驗結論

這版把 adaptive fusion 後處理整合進 melody pipeline，並保留本機分析工具用來檢查與匯出報表：

- `scripts/postprocess_melody_debug.py`：從 `comparison.csv` 產生 `rmvpe_postprocessed`、`fusion_postprocessed` 與 `hybrid_postprocessed`
- `scripts/export_melody_excel.py`：匯出 Excel V4.2 報表，比較 raw backend、postprocessed lines、gap fill 與 note 統計
- `app/services/melody_fusion/postprocess.py`：pipeline 內使用的 postprocess implementation
- `requirements-dev.txt` 加入 `openpyxl>=3.1`
- `.gitignore` 忽略 `tests/output/` 報表輸出

目前第一版主旋律 / 簡譜主線不建議直接使用 raw fusion，也不建議只使用 raw RMVPE。主線暫定為：

```text
hybrid_postprocessed = RMVPE 為主，Fusion 只在 RMVPE 缺值、且其他模型支持時補洞
```

V4.2 已驗證的實驗流程：

```text
RMVPE
torchcrepe
FCPE
PESTO
↓
adaptive fusion
↓
rmvpe_postprocessed
fusion_postprocessed
hybrid_postprocessed
↓
Excel V4.2 分析
```

三條候選線的判斷：

| 候選線 | 特色 | 結論 |
| --- | --- | --- |
| `rmvpe_postprocessed` | 音高最穩，但 raw notes 很碎 | 適合當主 anchor |
| `fusion_postprocessed` | note 數較乾淨，但 voiced coverage 較低 | 不適合單獨當主線 |
| `hybrid_postprocessed` | RMVPE 為主，只用 fusion 補有支持的空白 | 目前最適合當第一版主線 |

關鍵數據：

```text
RMVPE 原始 notes: 769
RMVPE 後處理 notes: 349

Fusion 原始 notes: 445
Fusion 後處理 notes: 339

RMVPE primary frames: 19209
Fusion gap fill frames: 184
Fusion gap rejected frames: 16

RMVPE postprocessed notes: 349
Hybrid postprocessed notes: 352
```

Hybrid 只比 RMVPE 多補約 1.84 秒，note 數只增加 3 個，代表它沒有讓旋律線明顯變碎，同時保留 fusion 在 RMVPE 缺值時的少量補強價值。

目前主線 pipeline：

```text
vocals.wav
↓
RMVPE / torchcrepe / FCPE / PESTO
↓
adaptive fusion
↓
postprocess
↓
hybrid_postprocessed
↓
後續 rhythm / beat / notation
```

Rhythm pipeline 會優先使用 `analysis/melody/fusion/postprocessed.csv` 的 `hybrid_postprocessed_midi` / `hybrid_postprocessed_f0_hz`。如果 postprocessed artifact 不存在，才 fallback 到 raw fusion CSV / JSON，並在 `rhythm_diagnostics.json` 記錄 `postprocessed_artifact_used` 與 `fallback_to_raw_fusion`。

節奏與切分主線：

```text
accompaniment.wav → beat / tempo / 小節網格
vocals.wav        → vocal onset / syllable onset
hybrid_postprocessed → 主旋律音高線
```

簡譜前主線暫定為：

```text
hybrid_postprocessed
+
beat grid
+
vocal onset
↓
hybrid_rhythm_quantized
↓
numbered_notation.json
```

更多細節：

- [docs/gpu.md](docs/gpu.md)：GPU venv、環境隔離、smoke tests、疑難排查
- [docs/melody_fusion.md](docs/melody_fusion.md)：CSV schema、fusion artifacts、CLI、debug output

## Rhythm Pipeline 與簡譜草稿

Rhythm Pipeline 會把既有音訊與 pitch artifacts 串成節奏檢查資料。當 melody fusion 成功或命中已快取的 melody variant 時，後端會 best-effort 產生新版簡譜草稿 artifacts；失敗時只記錄 warning，不會讓 melody job 失敗，也不會覆蓋 `melody.json` / `melody.mid`。

Phase 2.2.3 後，`notes_draft` 的 raw segmentation 會用 boundary decision 與 segment 內 pitch plateau 檢查，避免把明確的大幅音高轉移誤判成 vibrato 或 tail drift。`vibrato_or_tail_drift_suppressed` 只應用在同一個音附近的小幅擺動；若 local pitch range 或 segment pitch stability 顯示已跨過大型音高區間，會改以 `large_pitch_transition` / `intra_segment_pitch_plateau` 等 reason 切分，而不是保留成單一不穩定 note。

目前 rhythm artifacts：

- `analysis/rhythm/beat_grid.json`：由 accompaniment 優先、mono audio fallback 產生的 beat / tempo / meter 初版結果
- `analysis/rhythm/vocal_onsets.csv`：由 vocals 優先、mono audio fallback 產生的 vocal onset 候選切點
- `analysis/rhythm/notes_draft.json`：由 postprocessed pitch timeline、beat grid、vocal onsets 組成的草稿音符 JSON
- `analysis/rhythm/notes_draft.csv`：方便人工檢查的草稿音符表格，包含 boundary reasons、boundary confidence、segment frame count 與 pitch stability
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

前端在 Step 2 區塊會依 artifacts 顯示人聲分離、四 backend pitch、fusion、postprocess、rhythm、notes 與 notation 的進度。完成後優先顯示 `jianpu_draft.txt`，並提供簡譜 TXT 下載；如果新版 artifact 無法讀取，仍會回退到既有 melody preview。

`notes_draft` segmentation 目前的主要保護：

- 大幅 pitch range 或 `pitch_stability_cents > 250` 的候選 segment 會二次檢查內部 plateau
- `>= 0.10s` 且音高差距明確的穩定 plateau 可切成獨立 raw segment
- 真正的小幅 vibrato / tail drift 仍可維持單一 segment
- `duration < 0.06s` 的極短 spike、octave spike 與大量短碎音仍會被壓制
- boundary metadata 會保留在 JSON / CSV，方便 debug 為何某個音被切分或合併

手動從 `notes_draft.json` 產生簡譜草稿：

```bash
python scripts/build_jianpu_draft.py --job-dir <JOB_DIR> --key C --mode major
```

手動匯出 rhythm debug 檔：

```bash
python scripts/export_rhythm_debug.py --job-dir <JOB_DIR> --out /tmp/rhythm-debug
```

輸出包含：

- `rhythm_summary.json`
- `beat_grid_preview.csv`
- `vocal_onsets_preview.csv`
- `notes_draft_preview.csv`
- `rhythm_quality_report.txt`

針對指定時間窗檢查 raw segmentation / final notes：

```bash
python scripts/debug_note_segment_window.py \
  --job-dir /tmp/yt2mp3_debug1 \
  --output-dir /tmp/yt2mp3_debug \
  --copy-dir /mnt/d
```

比較兩份 `notes_draft.csv` 的音符數、短音與指定 window 差異：

```bash
python scripts/compare_notes_draft.py \
  --baseline /path/to/Phase_2.2.1_notes_draft.csv \
  --candidate /tmp/yt2mp3_debug/Phase_2.2.3_notes_draft.csv
```

目前限制：

- beat grid 目前是初版
- auto meter 不一定會自動判斷小節
- `6/8` 採保守 pulse 假設
- vocal onset 是候選切點，不是最終音符邊界
- `notes_draft` 是草稿音符，不是正式簡譜；Phase 2.2.3 已改善大型 pitch transition，但仍可能需要人工檢查尾音滑音與裝飾音
- `numbered_notation.json` 與 `jianpu_draft.txt` 仍是自動草稿，可能需要人工校正
- 不會輸出正式 MusicXML / PDF
- 不會覆蓋 `melody.json` / `melody.mid`

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

完整範例見 [.env.example](.env.example)。

## 測試與 Smoke

主環境測試：

```bash
ruff check app tests scripts
ruff format --check app tests scripts
pytest -q
```

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
python scripts/postprocess_melody_debug.py --artifact-root /tmp/yt2mp3-fusion-debug
python scripts/export_rhythm_debug.py --job-dir /path/to/job --out /tmp/rhythm-debug
python scripts/build_jianpu_draft.py --job-dir /path/to/job --key C --mode major
python scripts/debug_note_segment_window.py --help
python scripts/compare_notes_draft.py --help
```

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

- [docs/index.md](docs/index.md)：文件一致性規則
- [docs/gpu.md](docs/gpu.md)：Host GPU runtime 詳細工程文件
- [docs/melody_fusion.md](docs/melody_fusion.md)：Adaptive melody fusion 契約
- [docs/yt2mp3-spec.md](docs/yt2mp3-spec.md)：歷史 MVP 產品與技術規格

依賴檔：

- `requirements.txt`：主 FastAPI runtime
- `requirements-dev.txt`：本機開發與測試工具
- `requirements-gpu.txt`：host GPU venv；不要安裝進主 runtime

依賴或系統套件變更時，請同步檢查 `THIRD_PARTY_NOTICES.md`。
