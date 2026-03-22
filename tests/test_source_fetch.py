from mathgent.sandbox.source_fetch import (
    build_source_resolution_code,
    is_archive_member_safe,
    payload_block_reason,
    score_latex_text,
)


def test_payload_block_reason_detects_recaptcha_and_html() -> None:
    assert payload_block_reason(b"...g-recaptcha...", "application/octet-stream") == "recaptcha"
    assert payload_block_reason(b"<html><body>blocked</body></html>", "text/html") == "html_page"
    assert payload_block_reason(b"plain tar bytes", "application/x-tar") is None


def test_archive_member_safety_rejects_traversal_and_absolute_paths(tmp_path) -> None:
    base = str(tmp_path)
    assert is_archive_member_safe(base, "main.tex")
    assert is_archive_member_safe(base, "nested/paper.tex")
    assert not is_archive_member_safe(base, "../evil.tex")
    assert not is_archive_member_safe(base, "/etc/passwd")
    assert not is_archive_member_safe(base, "")


def test_score_latex_text_prefers_structured_theorem_content() -> None:
    weak_text = "Just plain text"
    rich_text = "\\documentclass{article}\\n\\begin{document}\\n\\begin{lemma}A\\end{lemma}"
    weak_score = score_latex_text(weak_text)
    rich_score = score_latex_text(rich_text)
    assert rich_score > weak_score


def test_generated_source_fetch_code_contains_safety_checks() -> None:
    code = build_source_resolution_code("2401.00001")
    assert "safe_extract_tar" in code
    assert "is_archive_member_safe" in code
    assert "Unsupported archive link member" in code
    assert "Unsafe archive member path" in code
    assert "payload_block_reason" in code
