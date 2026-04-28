from services.observability import bounded_text, safe_json_preview


def test_bounded_text_marks_truncation_with_original_length() -> None:
    text = bounded_text("abcdefghijklmnopqrstuvwxyz", max_chars=20)

    assert len(text) == 20
    assert "truncated" in text

    longer = bounded_text("x" * 120, max_chars=80)
    assert longer.endswith("[truncated chars=120 max=80]")


def test_safe_json_preview_marks_truncation() -> None:
    preview = safe_json_preview({"message": "x" * 100}, max_chars=60)

    assert len(preview) <= 60
    assert "truncated" in preview
