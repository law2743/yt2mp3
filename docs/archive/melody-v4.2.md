# Melody V4.2 Archive

本文件封存 Melody V4.2 研發與實驗結論，作為歷史紀錄。`docs/archive/` 內文件與腳本為歷史研發紀錄，不代表目前 active runtime。

## 定位

Melody V4.2 把 adaptive fusion 後處理整合進 melody pipeline，並保留本機分析工具用來檢查與匯出報表：

- `docs/archive/scripts/postprocess_melody_debug.py`：從 `comparison.csv` 產生 `rmvpe_postprocessed`、`fusion_postprocessed` 與 `hybrid_postprocessed`
- `docs/archive/scripts/export_melody_excel.py`：匯出 Excel V4.2 報表，比較 raw backend、postprocessed lines、gap fill 與 note 統計
- `app/services/melody_fusion/postprocess.py`：pipeline 內使用的 postprocess implementation

當時的第一版主旋律 / 簡譜主線不建議直接使用 raw fusion，也不建議只使用 raw RMVPE。主線暫定為：

```text
hybrid_postprocessed = RMVPE 為主，Fusion 只在 RMVPE 缺值、且其他模型支持時補洞
```

## 實驗流程

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
| `hybrid_postprocessed` | RMVPE 為主，只用 fusion 補有支持的空白 | 當時最適合當第一版主線 |

## 關鍵數據

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

## 當時主線

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
