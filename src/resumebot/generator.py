"""Step 2: Generate a tailored resume from a JD + the user's details.

Takes the LaTeX template produced in step 1 and refills it with the user's real
information, tailored to a specific job description, while preserving the
template's exact visual design.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .claude_client import ClaudeClient
from .textutils import (
    extract_latex,
    freeze_preamble,
    recombine_document,
    splice_selected_sections,
    split_document,
)

# Sections the user can choose to tailor. Order is the typical resume order.
AVAILABLE_SECTIONS: list[str] = [
    "Profile Summary",
    "Work Experience",
    "Skills",
    "Projects",
    "Education",
    "Certifications",
]

_SYSTEM = (
    "You are an elite resume writer and LaTeX typesetter. You craft compelling, "
    "ORIGINAL resume copy tailored to a job description — never copy-pasting the "
    "candidate's raw notes verbatim — while reproducing a given LaTeX design "
    "EXACTLY (fonts, rules/lines, colours, spacing, layout) and keeping the "
    "result on a single, clean page. You are truthful: you reframe and sharpen "
    "real experience, but never invent employers, dates, degrees, or metrics."
)

_PROMPT = r"""
You are given THREE things:

1. A LaTeX RESUME TEMPLATE whose visual design must be preserved exactly
   (same packages, macros, layout, spacing, headings, bullet style, fonts).
2. A TARGET JOB DESCRIPTION the resume should be tailored to.
3. The CANDIDATE'S DETAILS (raw experience, skills, education, contact info, etc).

Produce a NEW, complete, compilable LaTeX resume that:

FORMATTING — DO NOT CHANGE IT (this is the strictest rule):
- The PREAMBLE (everything from \documentclass up to and including
  \begin{document}) defines the entire visual design: packages, \geometry/
  margins, fonts, colours, horizontal rules/lines, section-heading style, bullet
  style, and all spacing/macros. You MUST copy the preamble VERBATIM, byte-for-
  byte. Do not add, remove, reorder, or modify a single line of it.
- Only edit the DOCUMENT BODY (between \begin{document} and \end{document}), and
  even there only the TEXT. Reuse the template's existing macros/environments
  (e.g. \resumeentry) exactly as defined — do not invent new formatting, change
  spacing, alter colours, or add/remove horizontal lines.
- The output's layout, fonts, rules, and spacing must be visually identical to
  the template. The reader must not be able to tell the formatting changed.
- The result MUST fit on ONE single page (unless the template is clearly a
  multi-page CV). Achieve this by CUTTING/TIGHTENING CONTENT, never by changing
  geometry, font size, or spacing macros. Do not let it spill to a second page,
  and do not leave it looking sparse.
- No overfull lines, no content running into the margin.

CONTENT — write it ORIGINALLY, do not copy-paste:
- Treat the candidate's details as raw source material, NOT final copy. Rewrite
  everything in your own polished, professional wording. The reader must never
  feel that notes were pasted in verbatim.
- Rewrite each work-experience bullet as a strong achievement statement using
  varied, powerful action verbs (avoid repeating the same verb) in the pattern:
  action -> what you did -> tools/skills -> measurable impact. Reuse real metrics
  from the details; never fabricate numbers.
- Tailor to the JD intelligently: identify the role's key responsibilities,
  required skills, and keywords, then surface the candidate's most relevant
  experience first and weave matching keywords/skills in NATURALLY (only where
  the candidate genuinely has them — never claim skills they lack).
- Make each role distinct: vary sentence structure and verbs across bullets and
  across roles so it reads as thoughtfully written, not templated.
- Write a crisp, original professional summary (if the template has one) that
  positions the candidate for THIS job in 2-3 lines.
- Use ONLY facts present in the candidate's details (employers, titles, dates,
  degrees, metrics). Reframing and rephrasing is encouraged; inventing facts is
  forbidden.
- Escape LaTeX special characters in user content (&, %, $, #, _, {, }, ~, ^).
__STYLE_PROFILE__
__SCOPE_RULES__
FITTING TO ONE PAGE — do this by editing CONTENT only:
- Prefer keeping 3-5 of the strongest, most JD-relevant bullets per role and
  cutting the weakest ones, rather than shrinking fonts or margins.
- Keep the most recent / most relevant 2-4 roles; summarise or drop older,
  less relevant ones.
- Use concise, single-line bullets where possible. Avoid redundant phrasing.
- NEVER change page geometry, font size, or the template's spacing macros to
  make it fit.

=== LATEX TEMPLATE START ===
__TEMPLATE__
=== LATEX TEMPLATE END ===

=== JOB DESCRIPTION START ===
__JOB_DESCRIPTION__
=== JOB DESCRIPTION END ===

=== CANDIDATE DETAILS START ===
__DETAILS__
=== CANDIDATE DETAILS END ===
__USER_INSTRUCTIONS__
Output ONLY the complete LaTeX source inside a single ```latex code block, with
no commentary before or after.
"""


def generate_resume(
    client: ClaudeClient,
    *,
    template_latex: str,
    job_description: str,
    details: str,
    sections: Optional[Sequence[str]] = None,
    instructions: str = "",
    style_profile: str = "",
) -> str:
    """Return tailored LaTeX resume source.

    ``sections`` optionally limits which resume sections get tailored to the JD.
    When provided, only those sections are rewritten/updated; every other section
    is kept exactly as in the template. When ``None`` (default), the whole resume
    is tailored. ``instructions`` is optional free-text guidance from the user on
    writing style (e.g. how to phrase work experience, whether to mix or replace
    content, tone, etc.). ``style_profile`` is the Style Analyst agent's
    description of the uploaded resume's style, which the writer should emulate.
    """
    scope_rules = _build_scope_rules(sections)
    prompt = (
        _PROMPT.replace("__SCOPE_RULES__", scope_rules)
        .replace("__STYLE_PROFILE__", _build_style_profile(style_profile))
        .replace("__USER_INSTRUCTIONS__", _build_user_instructions(instructions))
        .replace("__TEMPLATE__", template_latex)
        .replace("__JOB_DESCRIPTION__", job_description)
        .replace("__DETAILS__", details)
    )
    response = client.ask(prompt, system=_SYSTEM)
    generated = extract_latex(response)
    # Hard guarantee #1: force the template's preamble (all formatting) back onto
    # the generated body, so the design can never drift no matter what the model did.
    frozen = freeze_preamble(template_latex, generated)

    # Hard guarantee #2: when specific sections were chosen, keep every OTHER
    # section (and the contact/header block) byte-for-byte from the template, and
    # only swap in the selected sections. This makes scoping deterministic instead
    # of trusting the model to leave untouched sections alone.
    if sections:
        selected = {s for s in AVAILABLE_SECTIONS if s in set(sections)}
        template_preamble, template_body = split_document(template_latex)
        _, generated_body = split_document(frozen)
        spliced = splice_selected_sections(template_body, generated_body, selected)
        if spliced is not None:
            frozen = recombine_document(template_preamble, spliced)

    return frozen


def _build_style_profile(style_profile: str) -> str:
    """Wrap the Style Analyst's profile for injection into the prompt."""
    text = (style_profile or "").strip()
    if not text:
        return ""
    return (
        "\nORIGINAL RESUME STYLE PROFILE (match this style — it describes HOW the\n"
        "uploaded resume is written and laid out; emulate its writing voice, tense,\n"
        "bullet anatomy, verb usage, metric phrasing, tone and formatting habits so\n"
        "the result reads in the SAME style, but with the candidate's own facts and\n"
        "NO copied wording). It must not override the truthfulness or formatting\n"
        "rules above:\n"
        f"{text}\n"
    )


def _build_user_instructions(instructions: str) -> str:
    """Wrap the user's free-text style guidance for injection into the prompt."""
    text = (instructions or "").strip()
    if not text:
        return ""
    return (
        "\nUSER STYLE INSTRUCTIONS (HIGH PRIORITY — follow these for HOW to write\n"
        "the content; they take precedence over the generic content guidance above,\n"
        "but must NOT override the formatting rules or the truthfulness rule — still\n"
        "no fabricated facts, and still keep the template's exact design):\n"
        f"{text}\n"
    )


def _build_scope_rules(sections: Optional[Sequence[str]]) -> str:
    """Build prompt rules restricting which sections may be changed."""
    if not sections:
        return ""  # tailor everything

    selected = [s for s in AVAILABLE_SECTIONS if s in set(sections)]
    if not selected:
        return ""

    selected_list = ", ".join(selected)
    return (
        "\nSECTION SCOPE (IMPORTANT — restrict your edits):\n"
        f"- ONLY update/tailor these sections to the job description: {selected_list}.\n"
        "- For those sections, refill them with the candidate's details and tailor\n"
        "  the wording to the JD as described above.\n"
        "- Keep EVERY OTHER section EXACTLY as it appears in the template — same\n"
        "  text, ordering, and formatting. Do not rewrite, reorder, or retitle them.\n"
        "- If a section to update has no matching info in the candidate's details,\n"
        "  leave the template's content for it unchanged rather than inventing.\n"
    )

