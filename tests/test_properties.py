from hypothesis import given, strategies as st

from pullback.discovery.arxiv.ids import normalize_arxiv_id
from pullback.extraction.parsing import extract_environment_name, parse_grep_headers, window_bounds


@given(
    total_lines=st.integers(min_value=1, max_value=10_000),
    center=st.integers(min_value=-50_000, max_value=50_000),
    radius=st.integers(min_value=0, max_value=200),
)
def test_window_bounds_stay_in_file(total_lines: int, center: int, radius: int) -> None:
    start, end = window_bounds(line_number=center, total_lines=total_lines, radius=radius)

    assert 1 <= start <= end <= total_lines
    assert (end - start + 1) <= min(total_lines, 2 * radius + 1)


@given(
    line_num=st.integers(min_value=1, max_value=10_000),
    title=st.text(min_size=0, max_size=60),
)
def test_parse_grep_headers_round_trip(line_num: int, title: str) -> None:
    raw = f"{line_num}:\\begin{{lemma}}[{title}]"
    headers = parse_grep_headers(raw)
    assert len(headers) == 1
    assert headers[0].line_number == line_num
    assert "\\begin{lemma}" in headers[0].line


@given(
    base=st.from_regex(r"(?:\\d{4}\\.\\d{4,5}|[a-z\\-]+/\\d{7})", fullmatch=True),
    version=st.integers(min_value=1, max_value=25),
)
def test_normalize_arxiv_id_is_idempotent(base: str, version: int) -> None:
    with_version = f"{base}v{version}"
    normalized_once = normalize_arxiv_id(with_version)
    normalized_twice = normalize_arxiv_id(normalized_once)
    assert normalized_once == normalized_twice


def test_extract_environment_name_from_header() -> None:
    assert extract_environment_name(r"\begin{lemma}") == "lemma"
    assert extract_environment_name(r"\begin{theorem}") == "theorem"
