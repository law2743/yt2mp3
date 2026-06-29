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
    assert "notes_draft_csv_url" in app_js
    assert "新版簡譜草稿暫時無法讀取。" in app_js
    assert "Failed to load jianpu draft txt" in app_js
    assert "Failed to load numbered notation json" in app_js
    assert "data-legacy-notation" in app_js
    assert "data-notation-download" in app_js


def test_frontend_notation_styles_keep_long_text_scrollable_on_mobile():
    styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

    assert ".jianpu-draft-pre" in styles
    assert "overflow:auto" in styles
    assert "white-space:pre" in styles
    assert ".notation-downloads { display:grid; grid-template-columns:1fr;" in styles
