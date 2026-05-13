"""Heuristic theorem numbering and label extraction from LaTeX sources."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..observability import get_logger, trace_span
from ..sandbox import SandboxRunner
from .parsing import THEOREM_LIKE_KEYWORDS, normalize_environment_token

log = get_logger("extraction.numbering")

_LABEL_MAP = (
    ("theorem", "Theorem"),
    ("thm", "Theorem"),
    ("lemma", "Lemma"),
    ("lem", "Lemma"),
    ("proposition", "Proposition"),
    ("prop", "Proposition"),
    ("corollary", "Corollary"),
    ("cor", "Corollary"),
    ("claim", "Claim"),
)

_LABEL_KEYWORDS = tuple({item[1].lower() for item in _LABEL_MAP})
_DEFAULT_COUNT_LABELS = {
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "claim",
    "example",
    "remark",
    "definition",
    "notation",
}


@dataclass(frozen=True)
class _EnvDef:
    env: str
    title: str
    shared: str | None
    within: str | None
    numbered: bool


@dataclass(frozen=True)
class _EnvConfig:
    env: str
    label: str | None
    count_label: str | None
    counter: str
    within: str | None
    numbered: bool


def _label_for_env(env: str) -> str | None:
    lower = env.lower()
    for keyword, label in _LABEL_MAP:
        if keyword in lower:
            return label
    return None


def _label_from_title(title: str) -> str | None:
    cleaned = _clean_title(title)
    lowered = cleaned.lower()
    for keyword, label in _LABEL_MAP:
        if keyword in lowered:
            return label
    return None


def _clean_title(title: str) -> str:
    if not title:
        return ""
    # Strip LaTeX commands (best-effort) and collapse whitespace.
    without_commands = re.sub(r"\\[a-zA-Z*]+", " ", title)
    without_braces = re.sub(r"[{}]", " ", without_commands)
    return " ".join(without_braces.split())


def _load_count_labels() -> set[str]:
    raw = os.getenv("PULLBACK_NUMBERING_COUNT_LABELS")
    if raw:
        labels = {item.strip().lower() for item in raw.split(",") if item.strip()}
        if labels:
            return labels
    return set(_DEFAULT_COUNT_LABELS)


def _strip_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        out: list[str] = []
        escaped = False
        for ch in line:
            if ch == "%" and not escaped:
                break
            escaped = ch == "\\" and not escaped
            if escaped and ch != "\\":
                escaped = False
            out.append(ch)
        lines.append("".join(out))
    return "\n".join(lines)


def _consume_group(text: str, start: int, open_char: str, close_char: str) -> tuple[str | None, int]:
    if start >= len(text) or text[start] != open_char:
        return None, start
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == open_char:
            depth += 1
        elif text[idx] == close_char:
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx], idx + 1
    return None, start


def _skip_ws(text: str, idx: int) -> int:
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx


def _parse_newtheorem_defs(preamble: str) -> dict[str, _EnvDef]:
    defs: dict[str, _EnvDef] = {}
    idx = 0
    while True:
        pos = preamble.find("\\newtheorem", idx)
        if pos == -1:
            break
        i = pos + len("\\newtheorem")
        numbered = True
        if i < len(preamble) and preamble[i] == "*":
            numbered = False
            i += 1
        i = _skip_ws(preamble, i)
        env_raw, i = _consume_group(preamble, i, "{", "}")
        if not env_raw:
            idx = pos + 1
            continue
        env_raw_stripped = env_raw.strip()
        if env_raw_stripped.endswith("*"):
            env = env_raw_stripped.lower()
        else:
            env = normalize_environment_token(env_raw) or env_raw_stripped
        i = _skip_ws(preamble, i)
        shared = None
        if i < len(preamble) and preamble[i] == "[":
            shared_raw, i = _consume_group(preamble, i, "[", "]")
            if shared_raw:
                shared = normalize_environment_token(shared_raw) or shared_raw.strip()
        i = _skip_ws(preamble, i)
        title_raw, i = _consume_group(preamble, i, "{", "}")
        if not title_raw:
            idx = pos + 1
            continue
        i = _skip_ws(preamble, i)
        within = None
        if i < len(preamble) and preamble[i] == "[":
            within_raw, i = _consume_group(preamble, i, "[", "]")
            if within_raw:
                within = within_raw.strip().lower()
        defs[env] = _EnvDef(env=env, title=title_raw, shared=shared, within=within, numbered=numbered)
        idx = i
    return defs


def _parse_numberwithin(preamble: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    pattern = re.compile(r"\\numberwithin\{(?P<env>[^}]+)\}\{(?P<within>[^}]+)\}")
    for match in pattern.finditer(preamble):
        env_raw = match.group("env")
        within_raw = match.group("within")
        env = normalize_environment_token(env_raw) or env_raw.strip()
        within = within_raw.strip().lower()
        if env and within:
            mapping[env] = within
    return mapping


def _resolve_within(env: str, defs: dict[str, _EnvDef], numberwithin: dict[str, str], seen: set[str]) -> str | None:
    if env in seen:
        return None
    seen.add(env)
    env_def = defs.get(env)
    if not env_def:
        return None
    if env_def.within:
        return env_def.within
    if env in numberwithin:
        return numberwithin[env]
    if env_def.shared:
        return _resolve_within(env_def.shared, defs, numberwithin, seen)
    return None


def _build_env_configs(preamble: str) -> dict[str, _EnvConfig]:
    defs = _parse_newtheorem_defs(preamble)
    numberwithin = _parse_numberwithin(preamble)
    configs: dict[str, _EnvConfig] = {}
    for env, env_def in defs.items():
        title_clean = _clean_title(env_def.title)
        label = _label_from_title(env_def.title)
        if label is None:
            label = _label_for_env(env)
        count_label = title_clean or label
        counter = env_def.shared or env
        within = _resolve_within(env, defs, numberwithin, set())
        configs[env] = _EnvConfig(
            env=env,
            label=label,
            count_label=count_label,
            counter=counter,
            within=within,
            numbered=env_def.numbered,
        )
    return configs


def _is_theorem_like(env: str, label: str | None) -> bool:
    env_lower = env.lower()
    if any(keyword in env_lower for keyword in THEOREM_LIKE_KEYWORDS):
        return True
    if label and label.lower() in _LABEL_KEYWORDS:
        return True
    return False


def _parse_counter_styles(preamble: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    renew_pattern = re.compile(
        r"\\renewcommand\*?\s*\{\\the(?P<counter>[A-Za-z@]+)\}\s*"
        r"\{\\(?P<style>Roman|roman|Alph|alph|arabic)\{(?P<inner>[A-Za-z@]+)\}\s*\}"
    )
    for match in renew_pattern.finditer(preamble):
        counter = match.group("counter")
        inner = match.group("inner")
        style = match.group("style")
        if counter == inner:
            mapping[counter] = style
    def_pattern = re.compile(
        r"\\def\\the(?P<counter>[A-Za-z@]+)\s*"
        r"\{\\(?P<style>Roman|roman|Alph|alph|arabic)\{(?P<inner>[A-Za-z@]+)\}\s*\}"
    )
    for match in def_pattern.finditer(preamble):
        counter = match.group("counter")
        inner = match.group("inner")
        style = match.group("style")
        if counter == inner:
            mapping[counter] = style
    return mapping


def _parse_addtoreset(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    pattern = re.compile(r"\\@addtoreset\{(?P<counter>[^}]+)\}\{(?P<within>[^}]+)\}")
    for match in pattern.finditer(text):
        counter = (match.group("counter") or "").strip().lower()
        within = (match.group("within") or "").strip().lower()
        if counter and within:
            mapping[counter] = within
    return mapping


def _parse_counter_formats(text: str) -> dict[str, list[tuple[str, str | None]]]:
    formats: dict[str, list[tuple[str, str | None]]] = {}

    def register(counter: str, body: str) -> None:
        tokens = _tokenize_counter_format(counter, body)
        if tokens:
            formats[counter] = tokens

    idx = 0
    while True:
        pos = text.find("\\renewcommand", idx)
        if pos == -1:
            break
        i = pos + len("\\renewcommand")
        if i < len(text) and text[i] == "*":
            i += 1
        i = _skip_ws(text, i)
        target_raw, i = _consume_group(text, i, "{", "}")
        if not target_raw:
            idx = pos + 1
            continue
        match = re.fullmatch(r"\\the(?P<counter>[A-Za-z@]+)", target_raw.strip())
        if not match:
            idx = pos + 1
            continue
        counter = match.group("counter").lower()
        i = _skip_ws(text, i)
        body_raw, i = _consume_group(text, i, "{", "}")
        if not body_raw:
            idx = pos + 1
            continue
        register(counter, body_raw)
        idx = i

    pattern = re.compile(r"\\def\\the(?P<counter>[A-Za-z@]+)")
    for match in pattern.finditer(text):
        counter = match.group("counter").lower()
        i = match.end()
        i = _skip_ws(text, i)
        body_raw, _ = _consume_group(text, i, "{", "}")
        if body_raw:
            register(counter, body_raw)

    return formats


def _tokenize_counter_format(counter: str, body: str) -> list[tuple[str, str | None]]:
    tokens: list[tuple[str, str | None]] = []
    i = 0

    def push_literal(value: str) -> None:
        if not value:
            return
        if tokens and tokens[-1][0] == "literal":
            tokens[-1] = ("literal", tokens[-1][1] + value)
        else:
            tokens.append(("literal", value))

    while i < len(body):
        if body[i] != "\\":
            j = body.find("\\", i)
            if j == -1:
                push_literal(body[i:])
                break
            push_literal(body[i:j])
            i = j
            continue
        if body.startswith("\\thesection", i):
            tokens.append(("section", None))
            i += len("\\thesection")
            continue
        if body.startswith("\\thesubsection", i):
            tokens.append(("subsection", None))
            i += len("\\thesubsection")
            continue
        if body.startswith("\\thesubsubsection", i):
            tokens.append(("subsubsection", None))
            i += len("\\thesubsubsection")
            continue
        if body.startswith("\\thechapter", i):
            tokens.append(("chapter", None))
            i += len("\\thechapter")
            continue
        style_match = re.match(r"\\(arabic|Roman|roman|Alph|alph)\b", body[i:])
        if style_match:
            style = style_match.group(1)
            i += len(style) + 1
            i = _skip_ws(body, i)
            arg, i = _consume_group(body, i, "{", "}")
            if arg:
                target = arg.strip().lower()
                if target == counter:
                    tokens.append(("counter", style))
                elif target in {"section", "subsection", "subsubsection", "chapter"}:
                    tokens.append((target, style))
            continue
        # Unknown command; skip it and preserve as literal backslash.
        push_literal("\\")
        i += 1

    if not any(kind == "counter" for kind, _ in tokens):
        return []
    return tokens


def _to_alph(value: int, *, upper: bool) -> str:
    if value <= 0:
        return "0"
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if upper else "abcdefghijklmnopqrstuvwxyz"
    result = ""
    num = value
    while num > 0:
        num -= 1
        result = alphabet[num % 26] + result
        num //= 26
    return result


def _to_roman(value: int, *, upper: bool) -> str:
    if value <= 0:
        return "0"
    mapping = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    num = value
    result = []
    for value, symbol in mapping:
        while num >= value:
            result.append(symbol)
            num -= value
    roman = "".join(result)
    return roman if upper else roman.lower()


def _format_counter_value(value: int, style: str | None) -> str:
    if not style or style == "arabic":
        return str(value)
    if style in {"Roman", "roman"}:
        return _to_roman(value, upper=style == "Roman")
    if style in {"Alph", "alph"}:
        return _to_alph(value, upper=style == "Alph")
    return str(value)


def _format_with_tokens(
    tokens: list[tuple[str, str | None]],
    *,
    counter_value: int,
    section_no: int,
    subsection_no: int,
    subsubsection_no: int,
    chapter_no: int,
    counter_style: str | None,
    section_style: str | None,
    subsection_style: str | None,
    subsubsection_style: str | None,
    chapter_style: str | None,
) -> str:
    parts: list[str] = []
    for kind, style in tokens:
        if kind == "literal":
            parts.append(style or "")
            continue
        if kind == "counter":
            parts.append(_format_counter_value(counter_value, style or counter_style))
            continue
        if kind == "section":
            parts.append(_format_counter_value(section_no, style or section_style))
            continue
        if kind == "subsection":
            parts.append(_format_counter_value(subsection_no, style or subsection_style))
            continue
        if kind == "subsubsection":
            parts.append(_format_counter_value(subsubsection_no, style or subsubsection_style))
            continue
        if kind == "chapter":
            parts.append(_format_counter_value(chapter_no, style or chapter_style))
            continue
    return "".join(parts)


def _format_number(
    counter_value: int,
    *,
    within: str | None,
    section_no: int,
    subsection_no: int,
    subsubsection_no: int,
    chapter_no: int,
    counter_style: str | None = None,
    section_style: str | None = None,
    subsection_style: str | None = None,
    subsubsection_style: str | None = None,
    chapter_style: str | None = None,
) -> str:
    counter_label = _format_counter_value(counter_value, counter_style)
    section_label = _format_counter_value(section_no, section_style)
    subsection_label = _format_counter_value(subsection_no, subsection_style)
    subsubsection_label = _format_counter_value(subsubsection_no, subsubsection_style)
    chapter_label = _format_counter_value(chapter_no, chapter_style)

    if within == "section":
        return f"{section_label}.{counter_label}"
    if within == "subsection":
        return f"{section_label}.{subsection_label}.{counter_label}"
    if within == "subsubsection":
        return f"{section_label}.{subsection_label}.{subsubsection_label}.{counter_label}"
    if within == "chapter":
        return f"{chapter_label}.{counter_label}"
    return counter_label


def _extract_labels_from_lines(lines: Iterable[str], preamble: str) -> list[str]:
    lines = list(lines)
    source_text = preamble + "\n" + "\n".join(lines)
    chapter_re = re.compile(r"\\chapter(\\*?)\{", flags=re.IGNORECASE)
    section_re = re.compile(r"\\section(\\*?)\{", flags=re.IGNORECASE)
    subsection_re = re.compile(r"\\subsection(\\*?)\{", flags=re.IGNORECASE)
    subsubsection_re = re.compile(r"\\subsubsection(\\*?)\{", flags=re.IGNORECASE)
    begin_re = re.compile(r"\\+begin\{(?P<env>[^}]+)\}", flags=re.IGNORECASE)
    appendix_re = re.compile(r"\\appendix\b", flags=re.IGNORECASE)
    proclaim_re = re.compile(
        r"\\proclaim\s*(?:\{(?P<block>[^}]+)\}|(?P<inline>[^\n]+))",
        flags=re.IGNORECASE,
    )
    setcounter_re = re.compile(r"\\setcounter\{(?P<name>[^}]+)\}\{(?P<value>-?\d+)\}")
    addcounter_re = re.compile(r"\\addtocounter\{(?P<name>[^}]+)\}\{(?P<value>-?\d+)\}")
    stepcounter_re = re.compile(r"\\stepcounter\{(?P<name>[^}]+)\}")

    configs = _build_env_configs(source_text)
    counter_styles = _parse_counter_styles(source_text)
    addtoreset = _parse_addtoreset(source_text)
    counter_formats = _parse_counter_formats(source_text)

    chapter_no = 0
    section_no = 0
    subsection_no = 0
    subsubsection_no = 0
    counters: dict[str, int] = {}
    chapter_style = counter_styles.get("chapter", "arabic")
    section_style = counter_styles.get("section", "arabic")
    subsection_style = counter_styles.get("subsection", "arabic")
    subsubsection_style = counter_styles.get("subsubsection", "arabic")

    counter_within: dict[str, str] = {}
    for env_config in configs.values():
        if env_config.within:
            counter_within[env_config.counter] = env_config.within
    for counter, within in addtoreset.items():
        counter_within.setdefault(counter, within)

    def reset_counters(*, within_levels: set[str]) -> None:
        for counter_name, within in counter_within.items():
            if within in within_levels:
                counters[counter_name] = 0

    count_labels = _load_count_labels()
    labels: list[str] = []
    for line in lines:
        for match in proclaim_re.finditer(line):
            raw = (match.group("block") or match.group("inline") or "").strip()
            if not raw:
                continue
            raw = raw.rstrip(".")
            parts = raw.split()
            if len(parts) < 2:
                continue
            label = None
            number = ""
            first_token = parts[0].strip(".")
            if first_token and first_token[0].isdigit():
                number = first_token
                first = parts[1].lower().strip(".")
                remainder = parts[2:]
            else:
                first = parts[0].lower().strip(".")
                remainder = parts[1:]
            for keyword, canonical in _LABEL_MAP:
                if first == keyword or first.startswith(keyword):
                    label = canonical
                    break
            if not label:
                continue
            if not number:
                number = " ".join(remainder).strip()
            if number:
                labels.append(f"{label} {number}")
        if appendix_re.search(line):
            chapter_no = 0
            section_no = 0
            subsection_no = 0
            subsubsection_no = 0
            chapter_style = "Alph"
            section_style = "Alph"
            subsection_style = counter_styles.get("subsection", "arabic")
            subsubsection_style = counter_styles.get("subsubsection", "arabic")
            reset_counters(within_levels={"chapter", "section", "subsection", "subsubsection"})
        for match in setcounter_re.finditer(line):
            name = (match.group("name") or "").strip().lower()
            try:
                value = int(match.group("value"))
            except (TypeError, ValueError):
                continue
            if name == "chapter":
                chapter_no = value
                reset_counters(within_levels={"chapter", "section", "subsection", "subsubsection"})
            elif name == "section":
                section_no = value
                reset_counters(within_levels={"section", "subsection", "subsubsection"})
            elif name == "subsection":
                subsection_no = value
                reset_counters(within_levels={"subsection", "subsubsection"})
            elif name == "subsubsection":
                subsubsection_no = value
                reset_counters(within_levels={"subsubsection"})
            else:
                counters[name] = value
        for match in addcounter_re.finditer(line):
            name = (match.group("name") or "").strip().lower()
            try:
                delta = int(match.group("value"))
            except (TypeError, ValueError):
                continue
            if name == "chapter":
                chapter_no += delta
                reset_counters(within_levels={"chapter", "section", "subsection", "subsubsection"})
            elif name == "section":
                section_no += delta
                reset_counters(within_levels={"section", "subsection", "subsubsection"})
            elif name == "subsection":
                subsection_no += delta
                reset_counters(within_levels={"subsection", "subsubsection"})
            elif name == "subsubsection":
                subsubsection_no += delta
                reset_counters(within_levels={"subsubsection"})
            else:
                counters[name] = counters.get(name, 0) + delta
        for match in stepcounter_re.finditer(line):
            name = (match.group("name") or "").strip().lower()
            if name == "chapter":
                chapter_no += 1
                reset_counters(within_levels={"chapter", "section", "subsection", "subsubsection"})
            elif name == "section":
                section_no += 1
                reset_counters(within_levels={"section", "subsection", "subsubsection"})
            elif name == "subsection":
                subsection_no += 1
                reset_counters(within_levels={"subsection", "subsubsection"})
            elif name == "subsubsection":
                subsubsection_no += 1
                reset_counters(within_levels={"subsubsection"})
            else:
                counters[name] = counters.get(name, 0) + 1
        chap = chapter_re.search(line)
        if chap and not chap.group(1):
            chapter_no += 1
            section_no = 0
            subsection_no = 0
            subsubsection_no = 0
            reset_counters(within_levels={"chapter", "section", "subsection", "subsubsection"})
        sec = section_re.search(line)
        if sec and not sec.group(1):
            section_no += 1
            subsection_no = 0
            subsubsection_no = 0
            reset_counters(within_levels={"section", "subsection", "subsubsection"})
        sub = subsection_re.search(line)
        if sub and not sub.group(1):
            subsection_no += 1
            subsubsection_no = 0
            reset_counters(within_levels={"subsection", "subsubsection"})
        subsub = subsubsection_re.search(line)
        if subsub and not subsub.group(1):
            subsubsection_no += 1
            reset_counters(within_levels={"subsubsection"})

        for match in begin_re.finditer(line):
            raw_env = (match.group("env") or "").strip()
            if raw_env.endswith("*"):
                continue
            env_token = normalize_environment_token(raw_env)
            if not env_token:
                continue
            config = configs.get(env_token)
            if config and not config.numbered:
                continue
            label = config.label if config else _label_for_env(env_token)
            if config:
                counter_name = config.counter
            else:
                count_label = label
                if not count_label or count_label.lower() not in count_labels:
                    continue
                counter_name = env_token
            counters[counter_name] = counters.get(counter_name, 0) + 1
            counter_style = counter_styles.get(counter_name, "arabic")
            format_tokens = counter_formats.get(counter_name)
            if format_tokens:
                number = _format_with_tokens(
                    format_tokens,
                    counter_value=counters[counter_name],
                    section_no=section_no,
                    subsection_no=subsection_no,
                    subsubsection_no=subsubsection_no,
                    chapter_no=chapter_no,
                    counter_style=counter_style,
                    section_style=section_style,
                    subsection_style=subsection_style,
                    subsubsection_style=subsubsection_style,
                    chapter_style=chapter_style,
                )
            else:
                section_tokens = counter_formats.get("section")
                if section_tokens:
                    section_label = _format_with_tokens(
                        section_tokens,
                        counter_value=section_no,
                        section_no=section_no,
                        subsection_no=subsection_no,
                        subsubsection_no=subsubsection_no,
                        chapter_no=chapter_no,
                        counter_style=counter_styles.get("section", "arabic"),
                        section_style=section_style,
                        subsection_style=subsection_style,
                        subsubsection_style=subsubsection_style,
                        chapter_style=chapter_style,
                    )
                else:
                    section_label = _format_counter_value(section_no, section_style)
                subsection_tokens = counter_formats.get("subsection")
                if subsection_tokens:
                    subsection_label = _format_with_tokens(
                        subsection_tokens,
                        counter_value=subsection_no,
                        section_no=section_no,
                        subsection_no=subsection_no,
                        subsubsection_no=subsubsection_no,
                        chapter_no=chapter_no,
                        counter_style=counter_styles.get("subsection", "arabic"),
                        section_style=section_style,
                        subsection_style=subsection_style,
                        subsubsection_style=subsubsection_style,
                        chapter_style=chapter_style,
                    )
                else:
                    subsection_label = _format_counter_value(subsection_no, subsection_style)
                subsubsection_tokens = counter_formats.get("subsubsection")
                if subsubsection_tokens:
                    subsubsection_label = _format_with_tokens(
                        subsubsection_tokens,
                        counter_value=subsubsection_no,
                        section_no=section_no,
                        subsection_no=subsection_no,
                        subsubsection_no=subsubsection_no,
                        chapter_no=chapter_no,
                        counter_style=counter_styles.get("subsubsection", "arabic"),
                        section_style=section_style,
                        subsection_style=subsection_style,
                        subsubsection_style=subsubsection_style,
                        chapter_style=chapter_style,
                    )
                else:
                    subsubsection_label = _format_counter_value(subsubsection_no, subsubsection_style)
                chapter_tokens = counter_formats.get("chapter")
                if chapter_tokens:
                    chapter_label = _format_with_tokens(
                        chapter_tokens,
                        counter_value=chapter_no,
                        section_no=section_no,
                        subsection_no=subsection_no,
                        subsubsection_no=subsubsection_no,
                        chapter_no=chapter_no,
                        counter_style=counter_styles.get("chapter", "arabic"),
                        section_style=section_style,
                        subsection_style=subsection_style,
                        subsubsection_style=subsubsection_style,
                        chapter_style=chapter_style,
                    )
                else:
                    chapter_label = _format_counter_value(chapter_no, chapter_style)
                within = counter_within.get(counter_name)
                if within == "section":
                    number = f"{section_label}.{_format_counter_value(counters[counter_name], counter_style)}"
                elif within == "subsection":
                    number = f"{section_label}.{subsection_label}.{_format_counter_value(counters[counter_name], counter_style)}"
                elif within == "subsubsection":
                    number = f"{section_label}.{subsection_label}.{subsubsection_label}.{_format_counter_value(counters[counter_name], counter_style)}"
                elif within == "chapter":
                    number = f"{chapter_label}.{_format_counter_value(counters[counter_name], counter_style)}"
                else:
                    number = _format_counter_value(counters[counter_name], counter_style)
            if label and _is_theorem_like(env_token, label):
                labels.append(f"{label} {number}")

    return labels


def extract_theorem_labels_from_text(text: str) -> list[str]:
    """Return theorem-like labels (e.g., "Theorem 1.1") found in a LaTeX source."""
    cleaned = _strip_comments(text)
    if "\\begin{document}" in cleaned:
        preamble, body = cleaned.split("\\begin{document}", 1)
    else:
        preamble, body = cleaned, cleaned
    return _extract_labels_from_lines(body.splitlines(), preamble)


def _expand_inputs(text: str, base_dir: Path, seen: set[Path]) -> str:
    pattern = re.compile(r"\\(input|include)\{(?P<path>[^}]+)\}")

    def repl(match: re.Match[str]) -> str:
        raw_path = (match.group("path") or "").strip()
        if not raw_path:
            return ""
        candidates: list[Path] = []
        candidate = (base_dir / raw_path)
        if candidate.suffix:
            candidates.append(candidate)
        else:
            candidates.append(candidate.with_suffix(".tex"))
            candidates.append(candidate)
        for path in candidates:
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if resolved in seen or not path.exists():
                continue
            seen.add(resolved)
            try:
                content = path.read_text(errors="ignore")
            except Exception:
                return ""
            return _expand_inputs(content, path.parent, seen)
        return ""

    return pattern.sub(repl, text)


def extract_theorem_labels_from_file(path: Path) -> list[str]:
    """Return theorem-like labels from a LaTeX file, expanding basic \\input/\\include directives."""
    text = path.read_text(errors="ignore")
    expanded = _expand_inputs(text, path.parent, {path.resolve()})
    return extract_theorem_labels_from_text(expanded)


def _build_label_scan_command(path: str) -> str:
    return (
        "python - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import re\n"
        "\n"
        f"path = pathlib.Path({path!r})\n"
        f"keywords = {tuple(THEOREM_LIKE_KEYWORDS)!r}\n"
        f"label_map = {tuple(_LABEL_MAP)!r}\n"
        f"count_labels = set({tuple(sorted(_DEFAULT_COUNT_LABELS))!r})\n"
        "raw_count_labels = os.getenv('PULLBACK_NUMBERING_COUNT_LABELS', '')\n"
        "if raw_count_labels.strip():\n"
        "    count_labels = {item.strip().lower() for item in raw_count_labels.split(',') if item.strip()}\n"
        "\n"
        "def expand_inputs(raw_text, base_dir, seen):\n"
        "    pat = re.compile(r'\\\\(input|include)\\{(?P<path>[^}]+)\\}')\n"
        "    def repl(match):\n"
        "        raw_path = (match.group('path') or '').strip()\n"
        "        if not raw_path:\n"
        "            return ''\n"
        "        candidates = []\n"
        "        candidate = base_dir / raw_path\n"
        "        if candidate.suffix:\n"
        "            candidates.append(candidate)\n"
        "        else:\n"
        "            candidates.append(candidate.with_suffix('.tex'))\n"
        "            candidates.append(candidate)\n"
        "        for path in candidates:\n"
        "            try:\n"
        "                resolved = path.resolve()\n"
        "            except Exception:\n"
        "                resolved = path\n"
        "            if resolved in seen or not path.exists():\n"
        "                continue\n"
        "            seen.add(resolved)\n"
        "            try:\n"
        "                content = path.read_text(errors='ignore')\n"
        "            except Exception:\n"
        "                return ''\n"
        "            return expand_inputs(content, path.parent, seen)\n"
        "        return ''\n"
        "    return pat.sub(repl, raw_text)\n"
        "\n"
        "text = expand_inputs(path.read_text(errors='ignore'), path.parent, {path.resolve()})\n"
        "lines = text.splitlines()\n"
        "\n"
        "def strip_comments(raw):\n"
        "    cleaned = []\n"
        "    for line in raw.splitlines():\n"
        "        out = []\n"
        "        escaped = False\n"
        "        for ch in line:\n"
        "            if ch == '%' and not escaped:\n"
        "                break\n"
        "            escaped = ch == '\\\\' and not escaped\n"
        "            if escaped and ch != '\\\\':\n"
        "                escaped = False\n"
        "            out.append(ch)\n"
        "        cleaned.append(''.join(out))\n"
        "    return '\\n'.join(cleaned)\n"
        "\n"
        "cleaned = strip_comments(text)\n"
        "if '\\\\begin{document}' in cleaned:\n"
        "    preamble, body = cleaned.split('\\\\begin{document}', 1)\n"
        "else:\n"
        "    preamble, body = cleaned, cleaned\n"
        "\n"
        "source_text = preamble + '\\n' + body\n"
        "\n"
        "chapter_re = re.compile(r'\\\\chapter(\\\\*?)\\{', flags=re.IGNORECASE)\n"
        "section_re = re.compile(r'\\\\section(\\\\*?)\\{', flags=re.IGNORECASE)\n"
        "subsection_re = re.compile(r'\\\\subsection(\\\\*?)\\{', flags=re.IGNORECASE)\n"
        "subsubsection_re = re.compile(r'\\\\subsubsection(\\\\*?)\\{', flags=re.IGNORECASE)\n"
        "begin_re = re.compile(r'\\\\+begin\\{(?P<env>[^}]+)\\}', flags=re.IGNORECASE)\n"
        "appendix_re = re.compile(r'\\\\appendix\\\\b', flags=re.IGNORECASE)\n"
        "proclaim_re = re.compile(r'\\\\proclaim\\s*(?:\\{(?P<block>[^}]+)\\}|(?P<inline>[^\\n]+))', flags=re.IGNORECASE)\n"
        "setcounter_re = re.compile(r'\\\\setcounter\\{(?P<name>[^}]+)\\}\\{(?P<value>-?\\d+)\\}')\n"
        "addcounter_re = re.compile(r'\\\\addtocounter\\{(?P<name>[^}]+)\\}\\{(?P<value>-?\\d+)\\}')\n"
        "stepcounter_re = re.compile(r'\\\\stepcounter\\{(?P<name>[^}]+)\\}')\n"
        "valid_env = re.compile(r'^[a-z@][a-z0-9@:_-]*$', re.IGNORECASE)\n"
        "\n"
        "def normalize_env(token: str) -> str | None:\n"
        "    env = token.strip().lower()\n"
        "    while env.endswith('*'):\n"
        "        env = env[:-1].strip()\n"
        "    if not env or not valid_env.fullmatch(env):\n"
        "        return None\n"
        "    return env\n"
        "\n"
        "def label_for_env(env: str) -> str | None:\n"
        "    lower = env.lower()\n"
        "    for keyword, label in label_map:\n"
        "        if keyword in lower:\n"
        "            return label\n"
        "    return None\n"
        "\n"
        "def clean_title(title: str) -> str:\n"
        "    without_commands = re.sub(r'\\\\[a-zA-Z*]+', ' ', title)\n"
        "    without_braces = re.sub(r'[{}]', ' ', without_commands)\n"
        "    return ' '.join(without_braces.split())\n"
        "\n"
        "def parse_newtheorem(preamble_text: str):\n"
        "    defs = {}\n"
        "    idx = 0\n"
        "    while True:\n"
        "        pos = preamble_text.find('\\\\newtheorem', idx)\n"
        "        if pos == -1:\n"
        "            break\n"
        "        i = pos + len('\\\\newtheorem')\n"
        "        numbered = True\n"
        "        if i < len(preamble_text) and preamble_text[i] == '*':\n"
        "            numbered = False\n"
        "            i += 1\n"
        "        while i < len(preamble_text) and preamble_text[i].isspace():\n"
        "            i += 1\n"
        "        if i >= len(preamble_text) or preamble_text[i] != '{':\n"
        "            idx = pos + 1\n"
        "            continue\n"
        "        env = None\n"
        "        depth = 0\n"
        "        for j in range(i, len(preamble_text)):\n"
        "            if preamble_text[j] == '{':\n"
        "                depth += 1\n"
        "            elif preamble_text[j] == '}':\n"
        "                depth -= 1\n"
        "                if depth == 0:\n"
        "                    env = preamble_text[i + 1:j]\n"
        "                    i = j + 1\n"
        "                    break\n"
        "        if not env:\n"
        "            idx = pos + 1\n"
        "            continue\n"
        "        env_raw = env.strip()\n"
        "        if env_raw.endswith('*'):\n"
        "            env = env_raw.lower()\n"
        "        else:\n"
        "            env = normalize_env(env) or env_raw\n"
        "        while i < len(preamble_text) and preamble_text[i].isspace():\n"
        "            i += 1\n"
        "        shared = None\n"
        "        if i < len(preamble_text) and preamble_text[i] == '[':\n"
        "            depth = 0\n"
        "            for j in range(i, len(preamble_text)):\n"
        "                if preamble_text[j] == '[':\n"
        "                    depth += 1\n"
        "                elif preamble_text[j] == ']':\n"
        "                    depth -= 1\n"
        "                    if depth == 0:\n"
        "                        shared = preamble_text[i + 1:j]\n"
        "                        i = j + 1\n"
        "                        break\n"
        "            if shared:\n"
        "                shared = normalize_env(shared) or shared.strip()\n"
        "        while i < len(preamble_text) and preamble_text[i].isspace():\n"
        "            i += 1\n"
        "        if i >= len(preamble_text) or preamble_text[i] != '{':\n"
        "            idx = pos + 1\n"
        "            continue\n"
        "        title = None\n"
        "        depth = 0\n"
        "        for j in range(i, len(preamble_text)):\n"
        "            if preamble_text[j] == '{':\n"
        "                depth += 1\n"
        "            elif preamble_text[j] == '}':\n"
        "                depth -= 1\n"
        "                if depth == 0:\n"
        "                    title = preamble_text[i + 1:j]\n"
        "                    i = j + 1\n"
        "                    break\n"
        "        if not title:\n"
        "            idx = pos + 1\n"
        "            continue\n"
        "        while i < len(preamble_text) and preamble_text[i].isspace():\n"
        "            i += 1\n"
        "        within = None\n"
        "        if i < len(preamble_text) and preamble_text[i] == '[':\n"
        "            depth = 0\n"
        "            for j in range(i, len(preamble_text)):\n"
        "                if preamble_text[j] == '[':\n"
        "                    depth += 1\n"
        "                elif preamble_text[j] == ']':\n"
        "                    depth -= 1\n"
        "                    if depth == 0:\n"
        "                        within = preamble_text[i + 1:j].strip().lower()\n"
        "                        i = j + 1\n"
        "                        break\n"
        "        defs[env] = {'title': title, 'shared': shared, 'within': within, 'numbered': numbered}\n"
        "        idx = i\n"
        "    return defs\n"
        "\n"
        "def parse_numberwithin(preamble_text: str):\n"
        "    mapping = {}\n"
        "    pat = re.compile(r'\\\\numberwithin\\{(?P<env>[^}]+)\\}\\{(?P<within>[^}]+)\\}')\n"
        "    for match in pat.finditer(preamble_text):\n"
        "        env = normalize_env(match.group('env')) or match.group('env').strip()\n"
        "        within = match.group('within').strip().lower()\n"
        "        if env and within:\n"
        "            mapping[env] = within\n"
        "    return mapping\n"
        "\n"
        "def parse_counter_styles(preamble_text: str):\n"
        "    mapping = {}\n"
        "    pat = re.compile(\n"
        "        r'\\\\renewcommand\\*?\\s*\\{\\\\the(?P<counter>[A-Za-z@]+)\\}\\s*'\n"
        "        r'\\{\\\\(?P<style>Roman|roman|Alph|alph|arabic)\\{(?P<inner>[A-Za-z@]+)\\}\\s*\\}'\n"
        "    )\n"
        "    for match in pat.finditer(preamble_text):\n"
        "        counter = match.group('counter')\n"
        "        inner = match.group('inner')\n"
        "        style = match.group('style')\n"
        "        if counter == inner:\n"
        "            mapping[counter] = style\n"
        "    pat = re.compile(\n"
        "        r'\\\\def\\\\the(?P<counter>[A-Za-z@]+)\\s*'\n"
        "        r'\\{\\\\(?P<style>Roman|roman|Alph|alph|arabic)\\{(?P<inner>[A-Za-z@]+)\\}\\s*\\}'\n"
        "    )\n"
        "    for match in pat.finditer(preamble_text):\n"
        "        counter = match.group('counter')\n"
        "        inner = match.group('inner')\n"
        "        style = match.group('style')\n"
        "        if counter == inner:\n"
        "            mapping[counter] = style\n"
        "    return mapping\n"
        "\n"
        "def parse_addtoreset(text: str):\n"
        "    mapping = {}\n"
        "    pat = re.compile(r'\\\\@addtoreset\\{(?P<counter>[^}]+)\\}\\{(?P<within>[^}]+)\\}')\n"
        "    for match in pat.finditer(text):\n"
        "        counter = (match.group('counter') or '').strip().lower()\n"
        "        within = (match.group('within') or '').strip().lower()\n"
        "        if counter and within:\n"
        "            mapping[counter] = within\n"
        "    return mapping\n"
        "\n"
        "def skip_ws(text: str, idx: int) -> int:\n"
        "    while idx < len(text) and text[idx].isspace():\n"
        "        idx += 1\n"
        "    return idx\n"
        "\n"
        "def consume_group(text: str, start: int, open_char: str = '{', close_char: str = '}'):\n"
        "    if start >= len(text) or text[start] != open_char:\n"
        "        return None, start\n"
        "    depth = 0\n"
        "    for idx in range(start, len(text)):\n"
        "        if text[idx] == open_char:\n"
        "            depth += 1\n"
        "        elif text[idx] == close_char:\n"
        "            depth -= 1\n"
        "            if depth == 0:\n"
        "                return text[start + 1:idx], idx + 1\n"
        "    return None, start\n"
        "\n"
        "def tokenize_counter_format(counter: str, body: str):\n"
        "    tokens = []\n"
        "    i = 0\n"
        "    def push_literal(value: str):\n"
        "        if not value:\n"
        "            return\n"
        "        if tokens and tokens[-1][0] == 'literal':\n"
        "            tokens[-1] = ('literal', tokens[-1][1] + value)\n"
        "        else:\n"
        "            tokens.append(('literal', value))\n"
        "    while i < len(body):\n"
        "        if body[i] != '\\\\':\n"
        "            j = body.find('\\\\', i)\n"
        "            if j == -1:\n"
        "                push_literal(body[i:])\n"
        "                break\n"
        "            push_literal(body[i:j])\n"
        "            i = j\n"
        "            continue\n"
        "        if body.startswith('\\\\thesection', i):\n"
        "            tokens.append(('section', None))\n"
        "            i += len('\\\\thesection')\n"
        "            continue\n"
        "        if body.startswith('\\\\thesubsection', i):\n"
        "            tokens.append(('subsection', None))\n"
        "            i += len('\\\\thesubsection')\n"
        "            continue\n"
        "        if body.startswith('\\\\thesubsubsection', i):\n"
        "            tokens.append(('subsubsection', None))\n"
        "            i += len('\\\\thesubsubsection')\n"
        "            continue\n"
        "        if body.startswith('\\\\thechapter', i):\n"
        "            tokens.append(('chapter', None))\n"
        "            i += len('\\\\thechapter')\n"
        "            continue\n"
        "        style_match = re.match(r'\\\\(arabic|Roman|roman|Alph|alph)\\b', body[i:])\n"
        "        if style_match:\n"
        "            style = style_match.group(1)\n"
        "            i += len(style) + 1\n"
        "            i = skip_ws(body, i)\n"
        "            arg, i = consume_group(body, i, '{', '}')\n"
        "            if arg:\n"
        "                target = arg.strip().lower()\n"
        "                if target == counter:\n"
        "                    tokens.append(('counter', style))\n"
        "                elif target in {'section', 'subsection', 'subsubsection', 'chapter'}:\n"
        "                    tokens.append((target, style))\n"
        "            continue\n"
        "        push_literal('\\\\')\n"
        "        i += 1\n"
        "    if not any(kind == 'counter' for kind, _ in tokens):\n"
        "        return []\n"
        "    return tokens\n"
        "\n"
        "def parse_counter_formats(text: str):\n"
        "    formats = {}\n"
        "    def register(counter: str, body: str):\n"
        "        tokens = tokenize_counter_format(counter, body)\n"
        "        if tokens:\n"
        "            formats[counter] = tokens\n"
        "    idx = 0\n"
        "    while True:\n"
        "        pos = text.find('\\\\renewcommand', idx)\n"
        "        if pos == -1:\n"
        "            break\n"
        "        i = pos + len('\\\\renewcommand')\n"
        "        if i < len(text) and text[i] == '*':\n"
        "            i += 1\n"
        "        i = skip_ws(text, i)\n"
        "        target, i = consume_group(text, i, '{', '}')\n"
        "        if not target:\n"
        "            idx = pos + 1\n"
        "            continue\n"
        "        m = re.fullmatch(r'\\\\the(?P<counter>[A-Za-z@]+)', target.strip())\n"
        "        if not m:\n"
        "            idx = pos + 1\n"
        "            continue\n"
        "        counter = m.group('counter').lower()\n"
        "        i = skip_ws(text, i)\n"
        "        body, i = consume_group(text, i, '{', '}')\n"
        "        if not body:\n"
        "            idx = pos + 1\n"
        "            continue\n"
        "        register(counter, body)\n"
        "        idx = i\n"
        "    for match in re.finditer(r'\\\\def\\\\the(?P<counter>[A-Za-z@]+)', text):\n"
        "        counter = match.group('counter').lower()\n"
        "        i = skip_ws(text, match.end())\n"
        "        body, _ = consume_group(text, i, '{', '}')\n"
        "        if body:\n"
        "            register(counter, body)\n"
        "    return formats\n"
        "\n"
        "def to_alph(value: int, upper: bool) -> str:\n"
        "    if value <= 0:\n"
        "        return '0'\n"
        "    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if upper else 'abcdefghijklmnopqrstuvwxyz'\n"
        "    num = value\n"
        "    result = ''\n"
        "    while num > 0:\n"
        "        num -= 1\n"
        "        result = alphabet[num % 26] + result\n"
        "        num //= 26\n"
        "    return result\n"
        "\n"
        "def to_roman(value: int, upper: bool) -> str:\n"
        "    if value <= 0:\n"
        "        return '0'\n"
        "    mapping = [\n"
        "        (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),\n"
        "        (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),\n"
        "        (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'),\n"
        "    ]\n"
        "    num = value\n"
        "    result = []\n"
        "    for val, sym in mapping:\n"
        "        while num >= val:\n"
        "            result.append(sym)\n"
        "            num -= val\n"
        "    roman = ''.join(result)\n"
        "    return roman if upper else roman.lower()\n"
        "\n"
        "def format_counter(value: int, style: str | None) -> str:\n"
        "    if not style or style == 'arabic':\n"
        "        return str(value)\n"
        "    if style in {'Roman', 'roman'}:\n"
        "        return to_roman(value, upper=style == 'Roman')\n"
        "    if style in {'Alph', 'alph'}:\n"
        "        return to_alph(value, upper=style == 'Alph')\n"
        "    return str(value)\n"
        "\n"
        "def format_with_tokens(tokens, *, counter_value, section_no, subsection_no, subsubsection_no, chapter_no, counter_style, section_style, subsection_style, subsubsection_style, chapter_style):\n"
        "    parts = []\n"
        "    for kind, style in tokens:\n"
        "        if kind == 'literal':\n"
        "            parts.append(style or '')\n"
        "        elif kind == 'counter':\n"
        "            parts.append(format_counter(counter_value, style or counter_style))\n"
        "        elif kind == 'section':\n"
        "            parts.append(format_counter(section_no, style or section_style))\n"
        "        elif kind == 'subsection':\n"
        "            parts.append(format_counter(subsection_no, style or subsection_style))\n"
        "        elif kind == 'subsubsection':\n"
        "            parts.append(format_counter(subsubsection_no, style or subsubsection_style))\n"
        "        elif kind == 'chapter':\n"
        "            parts.append(format_counter(chapter_no, style or chapter_style))\n"
        "    return ''.join(parts)\n"
        "\n"
        "defs = parse_newtheorem(source_text)\n"
        "numberwithin = parse_numberwithin(source_text)\n"
        "counter_styles = parse_counter_styles(source_text)\n"
        "addtoreset = parse_addtoreset(source_text)\n"
        "counter_formats = parse_counter_formats(source_text)\n"
        "\n"
        "def resolve_within(env, seen=None):\n"
        "    if seen is None:\n"
        "        seen = set()\n"
        "    if env in seen:\n"
        "        return None\n"
        "    seen.add(env)\n"
        "    info = defs.get(env)\n"
        "    if not info:\n"
        "        return None\n"
        "    if info.get('within'):\n"
        "        return info['within']\n"
        "    if env in numberwithin:\n"
        "        return numberwithin[env]\n"
        "    shared = info.get('shared')\n"
        "    if shared:\n"
        "        return resolve_within(shared, seen)\n"
        "    return None\n"
        "\n"
        "configs = {}\n"
        "for env, info in defs.items():\n"
        "    label = None\n"
        "    title_clean = clean_title(info.get('title', ''))\n"
        "    lowered = title_clean.lower()\n"
        "    for keyword, label_name in label_map:\n"
        "        if keyword in lowered:\n"
        "            label = label_name\n"
        "            break\n"
        "    if label is None:\n"
        "        label = label_for_env(env)\n"
        "    counter = info.get('shared') or env\n"
        "    count_label = title_clean or label\n"
        "    configs[env] = {'label': label, 'count_label': count_label, 'counter': counter, 'within': resolve_within(env), 'numbered': info.get('numbered', True)}\n"
        "\n"
        "counter_within = {}\n"
        "for cfg in configs.values():\n"
        "    if cfg.get('within'):\n"
        "        counter_within[cfg['counter']] = cfg['within']\n"
        "for counter, within in addtoreset.items():\n"
        "    counter_within.setdefault(counter, within)\n"
        "\n"
        "chapter_no = 0\n"
        "section_no = 0\n"
        "subsection_no = 0\n"
        "subsubsection_no = 0\n"
        "counters = {}\n"
        "chapter_style = counter_styles.get('chapter', 'arabic')\n"
        "section_style = counter_styles.get('section', 'arabic')\n"
        "subsection_style = counter_styles.get('subsection', 'arabic')\n"
        "subsubsection_style = counter_styles.get('subsubsection', 'arabic')\n"
        "labels = []\n"
        "\n"
        "def reset_counters(levels):\n"
        "    for counter, within in counter_within.items():\n"
        "        if within in levels:\n"
        "            counters[counter] = 0\n"
        "\n"
        "def is_theorem_like(env, label):\n"
        "    if any(k in env for k in keywords):\n"
        "        return True\n"
        "    if label and label.lower() in {" + ",".join(repr(item) for item in _LABEL_KEYWORDS) + "}:\n"
        "        return True\n"
        "    return False\n"
        "\n"
        "for line in body.splitlines():\n"
        "    for match in proclaim_re.finditer(line):\n"
        "        raw = (match.group('block') or match.group('inline') or '').strip()\n"
        "        if not raw:\n"
        "            continue\n"
        "        raw = raw.rstrip('.')\n"
        "        parts = raw.split()\n"
        "        if len(parts) < 2:\n"
        "            continue\n"
        "        label = None\n"
        "        number = ''\n"
        "        first_token = parts[0].strip('.')\n"
        "        if first_token and first_token[0].isdigit():\n"
        "            number = first_token\n"
        "            first = parts[1].lower().strip('.')\n"
        "            remainder = parts[2:]\n"
        "        else:\n"
        "            first = parts[0].lower().strip('.')\n"
        "            remainder = parts[1:]\n"
        "        for keyword, canonical in label_map:\n"
        "            if first == keyword or first.startswith(keyword):\n"
        "                label = canonical\n"
        "                break\n"
        "        if not label:\n"
        "            continue\n"
        "        if not number:\n"
        "            number = ' '.join(remainder).strip()\n"
        "        if number:\n"
        "            labels.append(str(label) + ' ' + str(number))\n"
        "    if appendix_re.search(line):\n"
        "        chapter_no = 0\n"
        "        section_no = 0\n"
        "        subsection_no = 0\n"
        "        subsubsection_no = 0\n"
        "        chapter_style = 'Alph'\n"
        "        section_style = 'Alph'\n"
        "        subsection_style = counter_styles.get('subsection', 'arabic')\n"
        "        subsubsection_style = counter_styles.get('subsubsection', 'arabic')\n"
        "        reset_counters({'chapter', 'section', 'subsection', 'subsubsection'})\n"
        "    for match in setcounter_re.finditer(line):\n"
        "        name = (match.group('name') or '').strip().lower()\n"
        "        try:\n"
        "            value = int(match.group('value'))\n"
        "        except Exception:\n"
        "            continue\n"
        "        if name == 'chapter':\n"
        "            chapter_no = value\n"
        "            reset_counters({'chapter', 'section', 'subsection', 'subsubsection'})\n"
        "        elif name == 'section':\n"
        "            section_no = value\n"
        "            reset_counters({'section', 'subsection', 'subsubsection'})\n"
        "        elif name == 'subsection':\n"
        "            subsection_no = value\n"
        "            reset_counters({'subsection', 'subsubsection'})\n"
        "        elif name == 'subsubsection':\n"
        "            subsubsection_no = value\n"
        "            reset_counters({'subsubsection'})\n"
        "        else:\n"
        "            counters[name] = value\n"
        "    for match in addcounter_re.finditer(line):\n"
        "        name = (match.group('name') or '').strip().lower()\n"
        "        try:\n"
        "            delta = int(match.group('value'))\n"
        "        except Exception:\n"
        "            continue\n"
        "        if name == 'chapter':\n"
        "            chapter_no += delta\n"
        "            reset_counters({'chapter', 'section', 'subsection', 'subsubsection'})\n"
        "        elif name == 'section':\n"
        "            section_no += delta\n"
        "            reset_counters({'section', 'subsection', 'subsubsection'})\n"
        "        elif name == 'subsection':\n"
        "            subsection_no += delta\n"
        "            reset_counters({'subsection', 'subsubsection'})\n"
        "        elif name == 'subsubsection':\n"
        "            subsubsection_no += delta\n"
        "            reset_counters({'subsubsection'})\n"
        "        else:\n"
        "            counters[name] = counters.get(name, 0) + delta\n"
        "    for match in stepcounter_re.finditer(line):\n"
        "        name = (match.group('name') or '').strip().lower()\n"
        "        if name == 'chapter':\n"
        "            chapter_no += 1\n"
        "            reset_counters({'chapter', 'section', 'subsection', 'subsubsection'})\n"
        "        elif name == 'section':\n"
        "            section_no += 1\n"
        "            reset_counters({'section', 'subsection', 'subsubsection'})\n"
        "        elif name == 'subsection':\n"
        "            subsection_no += 1\n"
        "            reset_counters({'subsection', 'subsubsection'})\n"
        "        elif name == 'subsubsection':\n"
        "            subsubsection_no += 1\n"
        "            reset_counters({'subsubsection'})\n"
        "        else:\n"
        "            counters[name] = counters.get(name, 0) + 1\n"
        "    chap = chapter_re.search(line)\n"
        "    if chap and not chap.group(1):\n"
        "        chapter_no += 1\n"
        "        section_no = 0\n"
        "        subsection_no = 0\n"
        "        subsubsection_no = 0\n"
        "        reset_counters({'chapter', 'section', 'subsection', 'subsubsection'})\n"
        "    sec = section_re.search(line)\n"
        "    if sec and not sec.group(1):\n"
        "        section_no += 1\n"
        "        subsection_no = 0\n"
        "        subsubsection_no = 0\n"
        "        reset_counters({'section', 'subsection', 'subsubsection'})\n"
        "    sub = subsection_re.search(line)\n"
        "    if sub and not sub.group(1):\n"
        "        subsection_no += 1\n"
        "        subsubsection_no = 0\n"
        "        reset_counters({'subsection', 'subsubsection'})\n"
        "    subsub = subsubsection_re.search(line)\n"
        "    if subsub and not subsub.group(1):\n"
        "        subsubsection_no += 1\n"
        "        reset_counters({'subsubsection'})\n"
        "    for match in begin_re.finditer(line):\n"
        "        raw_env = (match.group('env') or '').strip()\n"
        "        if raw_env.endswith('*'):\n"
        "            continue\n"
        "        env = normalize_env(raw_env)\n"
        "        if not env:\n"
        "            continue\n"
        "        cfg = configs.get(env)\n"
        "        if cfg and not cfg.get('numbered', True):\n"
        "            continue\n"
        "        label = cfg['label'] if cfg else label_for_env(env)\n"
        "        if cfg:\n"
        "            counter = cfg['counter']\n"
        "        else:\n"
        "            count_label = label\n"
        "            if not count_label or count_label.lower() not in count_labels:\n"
        "                continue\n"
        "            counter = env\n"
        "        counters[counter] = counters.get(counter, 0) + 1\n"
        "        counter_style = counter_styles.get(counter, 'arabic')\n"
        "        format_tokens = counter_formats.get(counter)\n"
        "        if format_tokens:\n"
        "            number = format_with_tokens(\n"
        "                format_tokens,\n"
        "                counter_value=counters[counter],\n"
        "                section_no=section_no,\n"
        "                subsection_no=subsection_no,\n"
        "                subsubsection_no=subsubsection_no,\n"
        "                chapter_no=chapter_no,\n"
        "                counter_style=counter_style,\n"
        "                section_style=section_style,\n"
        "                subsection_style=subsection_style,\n"
        "                subsubsection_style=subsubsection_style,\n"
        "                chapter_style=chapter_style,\n"
        "            )\n"
        "        else:\n"
        "            section_tokens = counter_formats.get('section')\n"
        "            if section_tokens:\n"
        "                section_label = format_with_tokens(\n"
        "                    section_tokens,\n"
        "                    counter_value=section_no,\n"
        "                    section_no=section_no,\n"
        "                    subsection_no=subsection_no,\n"
        "                    subsubsection_no=subsubsection_no,\n"
        "                    chapter_no=chapter_no,\n"
        "                    counter_style=counter_styles.get('section', 'arabic'),\n"
        "                    section_style=section_style,\n"
        "                    subsection_style=subsection_style,\n"
        "                    subsubsection_style=subsubsection_style,\n"
        "                    chapter_style=chapter_style,\n"
        "                )\n"
        "            else:\n"
        "                section_label = format_counter(section_no, section_style)\n"
        "            subsection_tokens = counter_formats.get('subsection')\n"
        "            if subsection_tokens:\n"
        "                subsection_label = format_with_tokens(\n"
        "                    subsection_tokens,\n"
        "                    counter_value=subsection_no,\n"
        "                    section_no=section_no,\n"
        "                    subsection_no=subsection_no,\n"
        "                    subsubsection_no=subsubsection_no,\n"
        "                    chapter_no=chapter_no,\n"
        "                    counter_style=counter_styles.get('subsection', 'arabic'),\n"
        "                    section_style=section_style,\n"
        "                    subsection_style=subsection_style,\n"
        "                    subsubsection_style=subsubsection_style,\n"
        "                    chapter_style=chapter_style,\n"
        "                )\n"
        "            else:\n"
        "                subsection_label = format_counter(subsection_no, subsection_style)\n"
        "            subsubsection_tokens = counter_formats.get('subsubsection')\n"
        "            if subsubsection_tokens:\n"
        "                subsubsection_label = format_with_tokens(\n"
        "                    subsubsection_tokens,\n"
        "                    counter_value=subsubsection_no,\n"
        "                    section_no=section_no,\n"
        "                    subsection_no=subsection_no,\n"
        "                    subsubsection_no=subsubsection_no,\n"
        "                    chapter_no=chapter_no,\n"
        "                    counter_style=counter_styles.get('subsubsection', 'arabic'),\n"
        "                    section_style=section_style,\n"
        "                    subsection_style=subsection_style,\n"
        "                    subsubsection_style=subsubsection_style,\n"
        "                    chapter_style=chapter_style,\n"
        "                )\n"
        "            else:\n"
        "                subsubsection_label = format_counter(subsubsection_no, subsubsection_style)\n"
        "            chapter_tokens = counter_formats.get('chapter')\n"
        "            if chapter_tokens:\n"
        "                chapter_label = format_with_tokens(\n"
        "                    chapter_tokens,\n"
        "                    counter_value=chapter_no,\n"
        "                    section_no=section_no,\n"
        "                    subsection_no=subsection_no,\n"
        "                    subsubsection_no=subsubsection_no,\n"
        "                    chapter_no=chapter_no,\n"
        "                    counter_style=counter_styles.get('chapter', 'arabic'),\n"
        "                    section_style=section_style,\n"
        "                    subsection_style=subsection_style,\n"
        "                    subsubsection_style=subsubsection_style,\n"
        "                    chapter_style=chapter_style,\n"
        "                )\n"
        "            else:\n"
        "                chapter_label = format_counter(chapter_no, chapter_style)\n"
        "            counter_label = format_counter(counters[counter], counter_style)\n"
        "            within = counter_within.get(counter)\n"
        "            if within == 'section':\n"
        "                number = section_label + '.' + counter_label\n"
        "            elif within == 'subsection':\n"
        "                number = section_label + '.' + subsection_label + '.' + counter_label\n"
        "            elif within == 'subsubsection':\n"
        "                number = section_label + '.' + subsection_label + '.' + subsubsection_label + '.' + counter_label\n"
        "            elif within == 'chapter':\n"
        "                number = chapter_label + '.' + counter_label\n"
        "            else:\n"
        "                number = counter_label\n"
        "        if label and is_theorem_like(env, label):\n"
        "            labels.append(str(label) + ' ' + str(number))\n"
        "\n"
        "print(json.dumps(labels))\n"
        "PY"
    )


async def get_theorem_labels(sandbox: SandboxRunner, arxiv_id: str) -> list[str]:
    with trace_span("forager_tools.get_theorem_labels", arxiv_id=arxiv_id):
        path = await sandbox.resolve_paper_path(arxiv_id)
        command = _build_label_scan_command(path)
        raw = await sandbox.run_shell(command)
        payload = raw.strip()
        if not payload:
            return []
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            log.warning("theorem_labels.invalid_json arxiv_id={}", arxiv_id)
            return []
        if not isinstance(data, list):
            return []
        labels = [item for item in data if isinstance(item, str)]
        log.info("theorem_labels.found arxiv_id={} count={}", arxiv_id, len(labels))
        return labels
