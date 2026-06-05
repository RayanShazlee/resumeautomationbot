"""Small helpers for cleaning up Claude responses."""

from __future__ import annotations

import re
from typing import Optional


def extract_latex(response: str) -> str:
    """Pull a LaTeX document out of a model response.

    Handles three cases:
    1. Response wrapped in a ```latex ... ``` fenced block.
    2. Response wrapped in a generic ``` ... ``` fenced block.
    3. Raw LaTeX with no fences.
    """
    fenced = re.search(r"```(?:latex|tex)?\s*\n(.*?)```", response, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    # No fences: if it looks like a full document, return as-is.
    if "\\documentclass" in response:
        start = response.index("\\documentclass")
        end = response.rfind("\\end{document}")
        if end != -1:
            return response[start : end + len("\\end{document}")].strip()
        return response[start:].strip()

    return response.strip()


def split_document(latex: str) -> tuple[str, str]:
    """Split a LaTeX document into ``(preamble, body)``.

    ``preamble`` is everything up to and INCLUDING ``\\begin{document}`` (all the
    formatting: packages, geometry, fonts, colours, macro definitions, spacing).
    ``body`` is everything between ``\\begin{document}`` and ``\\end{document}``
    (the actual resume content). If the markers are missing, returns
    ``("", latex)``.
    """
    begin = re.search(r"\\begin\{document\}", latex)
    end = re.search(r"\\end\{document\}", latex)
    if not begin or not end:
        return "", latex
    preamble = latex[: begin.end()]
    body = latex[begin.end() : end.start()]
    return preamble, body.strip("\n")


def recombine_document(preamble: str, body: str) -> str:
    """Reattach a frozen ``preamble`` to a (possibly new) ``body``.

    Guarantees the formatting in ``preamble`` is preserved exactly, regardless of
    what the model returned around the body.
    """
    return f"{preamble}\n{body}\n\\end{{document}}\n"


def freeze_preamble(template_latex: str, generated_latex: str) -> str:
    """Force ``generated_latex`` to use the template's preamble.

    Takes the body from ``generated_latex`` and splices it onto the template's
    frozen preamble, so the output's formatting is byte-for-byte the template's.
    If either document lacks document markers, falls back to the generated text.
    """
    template_preamble, _ = split_document(template_latex)
    _, generated_body = split_document(generated_latex)
    if not template_preamble or not generated_body:
        return generated_latex
    return recombine_document(template_preamble, generated_body)


# Map of heading keywords (substring match, lower-case) -> canonical section name.
# Used to recognise which resume section a heading belongs to.
SECTION_KEYWORDS: dict[str, str] = {
    "professional summary": "Profile Summary",
    "career summary": "Profile Summary",
    "summary": "Profile Summary",
    "profile": "Profile Summary",
    "objective": "Profile Summary",
    "professional experience": "Work Experience",
    "work experience": "Work Experience",
    "work history": "Work Experience",
    "employment": "Work Experience",
    "experience": "Work Experience",
    "technical skills": "Skills",
    "core competencies": "Skills",
    "skills": "Skills",
    "projects": "Projects",
    "education": "Education",
    "academic": "Education",
    "certifications": "Certifications",
    "certification": "Certifications",
    "licenses": "Certifications",
}


def canonical_for_title(title: str) -> Optional[str]:
    """Return the canonical section name for a heading title, or ``None``."""
    cleaned = re.sub(r"[^a-z ]", "", title.strip().lower())
    for keyword, canonical in SECTION_KEYWORDS.items():
        if keyword in cleaned:
            return canonical
    return None


def split_body_sections(
    body: str,
) -> Optional[tuple[str, list[tuple[str, str, Optional[str]]]]]:
    """Split a document body into ``(header, sections)``.

    ``header`` is everything before the first recognised section heading (the
    name/contact block). ``sections`` is a list of ``(title, raw_text, canonical)``
    where ``raw_text`` includes the heading command and runs until the next
    section. Returns ``None`` if no section headings could be detected.
    """
    heading_re = re.compile(r"(\\[a-zA-Z@]+\*?)\s*\{([^{}]*)\}")

    # Find which command introduces section headings by counting how often each
    # command is followed by a recognisable section title.
    cmd_counts: dict[str, int] = {}
    for match in heading_re.finditer(body):
        if canonical_for_title(match.group(2)):
            cmd = match.group(1)
            cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1
    if not cmd_counts:
        return None

    section_cmd = max(cmd_counts, key=lambda c: cmd_counts[c])
    pat = re.compile(re.escape(section_cmd) + r"\s*\{([^{}]*)\}")
    matches = list(pat.finditer(body))
    if not matches:
        return None

    header = body[: matches[0].start()]
    sections: list[tuple[str, str, Optional[str]]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        title = match.group(1)
        sections.append((title, body[start:end], canonical_for_title(title)))
    return header, sections


def splice_selected_sections(
    template_body: str,
    generated_body: str,
    selected_canonicals: set[str],
) -> Optional[str]:
    """Keep template sections verbatim except the ``selected_canonicals``.

    Rebuilds the body using the template's header and section order. For each
    template section whose canonical name is in ``selected_canonicals`` AND has a
    matching section in ``generated_body``, the generated (tailored) text is used;
    every other section — and the header/contact block — is taken verbatim from
    the template. Returns ``None`` if either body can't be split into sections.
    """
    t = split_body_sections(template_body)
    g = split_body_sections(generated_body)
    if not t or not g:
        return None

    template_header, template_sections = t
    _, generated_sections = g

    generated_by_canon: dict[str, str] = {}
    for _title, text, canon in generated_sections:
        if canon and canon not in generated_by_canon:
            generated_by_canon[canon] = text

    out = template_header
    for _title, text, canon in template_sections:
        if canon and canon in selected_canonicals and canon in generated_by_canon:
            out += generated_by_canon[canon]
        else:
            out += text
    return out


