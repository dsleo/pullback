"""E2B code generator to fetch arXiv source archives and resolve the best TeX file path."""

from __future__ import annotations

from pathlib import Path

LATEX_MARKERS: tuple[str, ...] = (
    "\\begin{document}",
    "\\documentclass",
    "\\\\documentstyle",
    "\\section",
    "\\chapter",
    "\\title",
    "\\newtheorem",
    "\\begin{lemma}",
    "\\begin{proposition}",
    "\\begin{theorem}",
)


def payload_block_reason(content: bytes, content_type: str) -> str | None:
    probe = content[:20000].lower()
    lowered_type = content_type.lower()
    if b"g-recaptcha" in probe or b"arxiv recaptcha" in probe:
        return "recaptcha"
    if "text/html" in lowered_type and b"<html" in probe:
        return "html_page"
    return None


def is_archive_member_safe(base_dir: str, member_name: str) -> bool:
    if not member_name or member_name.startswith("/") or "\x00" in member_name:
        return False

    base = Path(base_dir).resolve()
    target = (base / member_name).resolve()
    try:
        return target.is_relative_to(base)
    except AttributeError:  # pragma: no cover - Python < 3.9 compatibility shim
        base_str = str(base)
        target_str = str(target)
        return target_str == base_str or target_str.startswith(base_str + "/")


def score_latex_text(text: str) -> tuple[int, int, int]:
    env_hits = (
        text.count("\\begin{lemma}")
        + text.count("\\begin{proposition}")
        + text.count("\\begin{theorem}")
    )
    structure_hits = sum(
        token in text
        for token in (
            "\\begin{document}",
            "\\documentclass",
            "\\\\documentstyle",
            "\\newtheorem",
            "\\section",
        )
    )
    return env_hits, structure_hits, len(text)


def build_source_resolution_code(arxiv_id: str) -> str:
    markers_literal = ", ".join(repr(marker) for marker in LATEX_MARKERS)
    return (
        "import gzip\n"
        "import pathlib\n"
        "import re\n"
        "import shutil\n"
        "import tarfile\n"
        "import urllib.error\n"
        "import urllib.request\n"
        "\n"
        f"arxiv_id = {arxiv_id!r}\n"
        "safe_id = re.sub(r'[^A-Za-z0-9._-]+', '_', arxiv_id)\n"
        "papers_root = pathlib.Path('/workspace/papers')\n"
        "papers_root.mkdir(parents=True, exist_ok=True)\n"
        "download_path = pathlib.Path('/tmp') / f'{safe_id}.src'\n"
        "extract_dir = pathlib.Path('/tmp') / f'{safe_id}_extract'\n"
        "if extract_dir.exists():\n"
        "    shutil.rmtree(extract_dir)\n"
        "extract_dir.mkdir(parents=True, exist_ok=True)\n"
        "\n"
        "def payload_block_reason(content: bytes, content_type: str) -> str | None:\n"
        "    probe = content[:20000].lower()\n"
        "    lowered_type = content_type.lower()\n"
        "    if b'g-recaptcha' in probe or b'arxiv recaptcha' in probe:\n"
        "        return 'recaptcha'\n"
        "    if 'text/html' in lowered_type and b'<html' in probe:\n"
        "        return 'html_page'\n"
        "    return None\n"
        "\n"
        "def is_archive_member_safe(base_dir: str, member_name: str) -> bool:\n"
        "    if not member_name or member_name.startswith('/') or '\\x00' in member_name:\n"
        "        return False\n"
        "    base = pathlib.Path(base_dir).resolve()\n"
        "    target = (base / member_name).resolve()\n"
        "    return target == base or str(target).startswith(str(base) + '/')\n"
        "\n"
        "def safe_extract_tar(tar: tarfile.TarFile, destination: pathlib.Path) -> None:\n"
        "    members = []\n"
        "    for member in tar.getmembers():\n"
        "        if member.issym() or member.islnk():\n"
        "            raise RuntimeError(f'Unsupported archive link member: {member.name}')\n"
        "        if not is_archive_member_safe(str(destination), member.name):\n"
        "            raise RuntimeError(f'Unsafe archive member path: {member.name}')\n"
        "        members.append(member)\n"
        "    tar.extractall(destination, members=members)\n"
        "\n"
        "def download_source_bytes(arxiv_id: str) -> bytes:\n"
        "    urls = [\n"
        "        f'https://export.arxiv.org/e-print/{arxiv_id}',\n"
        "        f'https://arxiv.org/e-print/{arxiv_id}',\n"
        "    ]\n"
        "    errors = []\n"
        "    for url in urls:\n"
        "        try:\n"
        "            req = urllib.request.Request(url, headers={'User-Agent': 'mathgent/0.1'})\n"
        "            with urllib.request.urlopen(req, timeout=60) as resp:\n"
        "                data = resp.read()\n"
        "                ctype = (resp.headers.get('Content-Type') or '').lower()\n"
        "        except urllib.error.HTTPError as exc:\n"
        "            errors.append(f'{url} -> HTTP {exc.code}')\n"
        "            continue\n"
        "        except Exception as exc:\n"
        "            errors.append(f'{url} -> {exc.__class__.__name__}: {exc}')\n"
        "            continue\n"
        "\n"
        "        reason = payload_block_reason(data, ctype)\n"
        "        if reason:\n"
        "            errors.append(f'{url} -> {reason}')\n"
        "            continue\n"
        "        return data\n"
        "\n"
        "    details = '; '.join(errors) if errors else 'unknown'\n"
        "    raise RuntimeError(f'Unable to download arXiv source for {arxiv_id}: {details}')\n"
        "\n"
        "content = download_source_bytes(arxiv_id)\n"
        "download_path.write_bytes(content)\n"
        "\n"
        "def is_latex_file(path: pathlib.Path) -> bool:\n"
        "    try:\n"
        "        text = path.read_text(errors='ignore')\n"
        "    except Exception:\n"
        "        return False\n"
        f"    markers = ({markers_literal},)\n"
        "    return any(marker in text for marker in markers)\n"
        "\n"
        "tex_files = []\n"
        "archive_unpacked = False\n"
        "try:\n"
        "    with tarfile.open(download_path, mode='r:*') as tar:\n"
        "        safe_extract_tar(tar, extract_dir)\n"
        "        archive_unpacked = True\n"
        "except tarfile.ReadError:\n"
        "    raw = download_path.read_bytes()\n"
        "    try:\n"
        "        raw = gzip.decompress(raw)\n"
        "    except Exception:\n"
        "        pass\n"
        "    fallback = extract_dir / f'{safe_id}.tex'\n"
        "    fallback.write_bytes(raw)\n"
        "\n"
        "for p in extract_dir.rglob('*.tex'):\n"
        "    if p.is_file():\n"
        "        tex_files.append(p)\n"
        "\n"
        "if not tex_files:\n"
        "    for pattern in ('*.ltx', '*.latex'):\n"
        "        for p in extract_dir.rglob(pattern):\n"
        "            if p.is_file():\n"
        "                tex_files.append(p)\n"
        "\n"
        "if not tex_files:\n"
        "    for p in extract_dir.rglob('*'):\n"
        "        if p.is_file() and is_latex_file(p):\n"
        "            tex_files.append(p)\n"
        "\n"
        "if not tex_files:\n"
        "    if archive_unpacked:\n"
        "        raise RuntimeError(f'No LaTeX-like file found in unpacked source for {arxiv_id}')\n"
        "    raise RuntimeError(f'No LaTeX source found for {arxiv_id}')\n"
        "\n"
        "def score_file(path: pathlib.Path) -> tuple[int, int, int]:\n"
        "    text = path.read_text(errors='ignore')\n"
        "    env_hits = text.count('\\\\begin{lemma}') + text.count('\\\\begin{proposition}') + text.count('\\\\begin{theorem}')\n"
        "    structure_hits = sum(token in text for token in ('\\\\begin{document}', '\\\\documentclass', '\\\\documentstyle', '\\\\newtheorem', '\\\\section'))\n"
        "    return (env_hits, structure_hits, len(text))\n"
        "\n"
        "selected = sorted(tex_files, key=score_file, reverse=True)[0]\n"
        "dest = papers_root / f'{safe_id}.tex'\n"
        "shutil.copyfile(selected, dest)\n"
        "\n"
        "# Cleanup temporary extraction artifacts immediately\n"
        "try:\n"
        "    if download_path.exists():\n"
        "        download_path.unlink()\n"
        "    if extract_dir.exists():\n"
        "        shutil.rmtree(extract_dir)\n"
        "except Exception:\n"
        "    pass\n"
        "\n"
        "print(str(dest))\n"
    )
