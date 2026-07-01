# Rhythm Phase 2.2.3 Archive

本文件封存 Rhythm Pipeline / Phase 2.2.3 segmentation 研發紀錄。`docs/archive/` 內文件與腳本為歷史研發紀錄，不代表目前 active runtime。

## Segmentation 改善

Phase 2.2.3 後，`notes_draft` 的 raw segmentation 會用 boundary decision 與 segment 內 pitch plateau 檢查，避免把明確的大幅音高轉移誤判成 vibrato 或 tail drift。

`vibrato_or_tail_drift_suppressed` 只應用在同一個音附近的小幅擺動；若 local pitch range 或 segment pitch stability 顯示已跨過大型音高區間，會改以 `large_pitch_transition` / `intra_segment_pitch_plateau` 等 reason 切分，而不是保留成單一不穩定 note。

`notes_draft` segmentation 當時的主要保護：

- 大幅 pitch range 或 `pitch_stability_cents > 250` 的候選 segment 會二次檢查內部 plateau
- `>= 0.10s` 且音高差距明確的穩定 plateau 可切成獨立 raw segment
- 真正的小幅 vibrato / tail drift 仍可維持單一 segment
- `duration < 0.06s` 的極短 spike、octave spike 與大量短碎音仍會被壓制
- boundary metadata 會保留在 JSON / CSV，方便 debug 為何某個音被切分或合併

## Rhythm Artifacts

Phase 2.2.3 使用或檢查的 rhythm artifacts：

- `analysis/rhythm/beat_grid.json`：由 accompaniment 優先、mono audio fallback 產生的 beat / tempo / meter 初版結果
- `analysis/rhythm/vocal_onsets.csv`：由 vocals 優先、mono audio fallback 產生的 vocal onset 候選切點
- `analysis/rhythm/notes_draft.json`：由 postprocessed pitch timeline、beat grid、vocal onsets 組成的草稿音符 JSON
- `analysis/rhythm/notes_draft.csv`：方便人工檢查的草稿音符表格，包含 boundary reasons、boundary confidence、segment frame count 與 pitch stability
- `analysis/rhythm/numbered_notation.json`：由 notes draft、調性與大小調轉出的新版簡譜結構化資料
- `analysis/rhythm/jianpu_draft.txt`：前端可預覽與下載的文字簡譜草稿
- `analysis/rhythm/rhythm_diagnostics.json`：pipeline backend、來源、fallback、warning 與 note stats 摘要

## 手動 Debug

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
python docs/archive/scripts/debug_note_segment_window.py \
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

## 限制

- beat grid 當時是初版
- auto meter 不一定會自動判斷小節
- `6/8` 採保守 pulse 假設
- vocal onset 是候選切點，不是最終音符邊界
- `notes_draft` 是草稿音符，不是正式簡譜；Phase 2.2.3 已改善大型 pitch transition，但仍可能需要人工檢查尾音滑音與裝飾音
- `numbered_notation.json` 與 `jianpu_draft.txt` 仍是自動草稿，可能需要人工校正
- 不會輸出正式 MusicXML / PDF
- 不會覆蓋 `melody.json` / `melody.mid`
