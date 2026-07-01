from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_renders_new_notation_draft_without_replacing_legacy_notation():
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    assert "function notationDraftPanel" in app_js
    assert "新版簡譜草稿" in app_js
    assert "此為自動產生的簡譜草稿，可能需要人工校正。" in app_js
    assert "loadNotationDraft(melody.notation_artifacts)" in app_js
    assert "const stepTwoAvailable = stemsAvailable || notationAvailable;" in app_js
    assert "function legacyMelodyNotationBlock" in app_js
    assert "data-legacy-notation" in app_js


def test_frontend_notation_downloads_and_fallbacks_are_present():
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    assert "jianpu_draft_txt_url" in app_js
    assert "numbered_notation_json_url" in app_js
    assert "新版簡譜草稿暫時無法讀取。" in app_js
    assert "Failed to load jianpu draft txt" in app_js
    assert "Failed to load numbered notation json" in app_js
    assert "data-legacy-notation" in app_js
    assert "data-notation-download" in app_js
    assert "下載簡譜草稿 TXT" in app_js
    assert "下載 numbered_notation.json" not in app_js
    assert "下載 notes_draft.csv" not in app_js
    assert "const response = await authenticatedFetch(artifacts.jianpu_draft_txt_url)" in app_js
    assert "target.textContent = text" in app_js
    assert "payload?.jianpu_text" in app_js


def test_frontend_step_two_uses_simple_artifact_based_status_wording():
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    for label in (
        "人聲分離",
        "RMVPE",
        "torchcrepe",
        "FCPE",
        "PESTO",
        "多模型融合",
        "旋律後處理",
        "節奏分析",
        "音符草稿",
        "新版簡譜",
    ):
        assert label in app_js
    for detail in (
        "正在分離人聲與伴奏，供後續旋律分析使用。",
        "正在使用 RMVPE 擷取人聲音高。",
        "正在使用 torchcrepe 交叉比對音高。",
        "正在使用 FCPE 補充音高候選。",
        "正在使用 PESTO 補充音高候選。",
        "正在整合四個模型的音高結果。",
        "正在清理跳音、短音與不穩定片段。",
        "正在分析 BPM、拍點與小節位置。",
        "正在把旋律整理成可量化的音符。",
        "正在產生新版簡譜草稿。",
    ):
        assert detail in app_js
    assert "function stepTwoStageFromArtifacts" in app_js
    assert "showStepTwoStatus(melody)" in app_js
    assert "正在建立 RMVPE 旋律工作" not in app_js
    assert "checklist" not in app_js.lower()
    assert "progress table" not in app_js.lower()


def test_frontend_notation_styles_keep_long_text_scrollable_on_mobile():
    styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

    assert ".jianpu-draft-pre" in styles
    assert "overflow-x:auto" in styles
    assert "white-space:pre-wrap" in styles
    assert ".notation-downloads { display:grid; grid-template-columns:1fr;" in styles
