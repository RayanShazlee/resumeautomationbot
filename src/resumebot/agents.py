"""Multi-agent style system.

Two cooperating Claude agents make a generated resume copy the *style* of the
uploaded resume "same to same":

1. ``StyleAnalystAgent`` reads the uploaded resume (PDF + the reconstructed
   LaTeX template) and produces a STYLE PROFILE — a structured description of
   both the visual design (fonts, colours, margins, headings, bullets, spacing)
   and the writing style (voice, tense, bullet phrasing, verb usage, metric
   emphasis, section conventions, length/density).

2. ``StyleMatcherAgent`` takes the freshly generated resume and, by COMPARING it
   against the original (using the style profile as a checklist plus a true
   visual diff of the two PDFs), edits the generated LaTeX so its style matches
   the original — WITHOUT copying the original's wording or facts.

``StyleAgentTeam`` is a thin coordinator that owns both agents and exposes the
high-level ``analyze`` / ``match`` steps the pipeline calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .claude_client import ClaudeClient
from .textutils import extract_latex


@dataclass
class StyleProfile:
    """The Style Analyst's findings about the uploaded resume."""

    text: str

    def __bool__(self) -> bool:  # truthy only when we actually have content
        return bool(self.text and self.text.strip())


# --------------------------------------------------------------------------- #
# Agent 1: Style Analyst                                                       #
# --------------------------------------------------------------------------- #

_ANALYST_SYSTEM = (
    "You are a resume style forensics expert. You study a resume and produce a "
    "precise, reusable STYLE PROFILE describing HOW it is designed and written — "
    "never its specific facts. You describe both the visual design and the "
    "writing voice so another writer could reproduce the exact same style with "
    "completely different content."
)

_ANALYST_PROMPT = r"""
The attached PDF is the candidate's uploaded resume (its LaTeX reconstruction is
shown below). Analyse it and produce a STYLE PROFILE that another writer can
follow to reproduce this resume's STYLE "same to same" with different content.

Describe ONLY style/format, NOT the specific facts. Cover, concisely:

VISUAL DESIGN
- Page: size, margins/density, single vs multi-column layout.
- Typography: font family (serif/sans), base size, and the relative sizes of the
  name, section headings, job titles, and body text; use of bold/italic/small-
  caps/letter-spacing.
- Colour: any accent colours (name, headings, rules, links) — note hex if visible.
- Section headings: text case, underline/rule style and thickness, spacing.
- Bullets: glyph used, indentation, and spacing within/between entries.
- Header/contact block: name placement, how contact items are separated, icons.

WRITING STYLE
- Voice & person (first person, implied first person, third person).
- Tense for current vs past roles.
- Bullet anatomy: typical pattern (e.g. "action verb -> what -> tools -> impact"),
  average bullet length, and how many bullets per role.
- Verb usage: are strong action verbs used? repeated or varied?
- Metric emphasis: how often numbers/percentages/$ impact appear and how phrased.
- Summary/objective: present or not, and its length/tone if present.
- Terminology level: technical vs business; keyword density; formality.
- Capitalisation/punctuation habits (e.g. periods at end of bullets, Title Case).

SECTION CONVENTIONS
- The exact section order and the heading wording used.
- Anything distinctive a reader would immediately recognise as "this resume's
  style".

=== LATEX RECONSTRUCTION OF THE UPLOADED RESUME ===
{template}
=== END LATEX RECONSTRUCTION ===

Output the STYLE PROFILE as clear, organised plain text under the headings
VISUAL DESIGN, WRITING STYLE, and SECTION CONVENTIONS. Use short bullet points.
Do NOT output LaTeX. Do NOT include the candidate's specific facts.
"""


class StyleAnalystAgent:
    """Agent that analyses the uploaded resume and returns a StyleProfile."""

    def __init__(self, client: ClaudeClient):
        self._client = client

    def analyze(
        self,
        *,
        template_latex: str,
        original_pdf: Optional[Path] = None,
    ) -> StyleProfile:
        """Produce a StyleProfile from the template LaTeX (and PDF if available).

        ``original_pdf`` is sent to Claude when present so the analyst can read
        the real rendered design, not just the LaTeX reconstruction.
        """
        prompt = _ANALYST_PROMPT.format(template=template_latex)
        pdf = Path(original_pdf) if original_pdf else None
        response = self._client.ask(prompt, pdf_path=pdf, system=_ANALYST_SYSTEM)
        return StyleProfile(text=response.strip())


# --------------------------------------------------------------------------- #
# Agent 2: Style Matcher                                                       #
# --------------------------------------------------------------------------- #

_MATCHER_SYSTEM = (
    "You are a meticulous resume editor. You COMPARE a generated resume against "
    "an original and rewrite the generated one so its FORMAT and STYLE match the "
    "original 100% — identical visual design and the same writing voice — while "
    "keeping the generated resume's own facts and wording. You never copy the "
    "original's text or facts; you only borrow its style. You also make the whole "
    "resume fit on a single page, like the original, by tightening content rather "
    "than altering the design."
)

_MATCHER_PROMPT = r"""
Two PDFs are attached:
- [ORIGINAL] = the uploaded resume whose FORMAT and STYLE must be matched 100%.
- [GENERATED] = the candidate's tailored resume (DIFFERENT content). Its current
  LaTeX source is shown below.

A STYLE PROFILE of [ORIGINAL] is also provided. Use it as a checklist AND do a
true visual diff of the two PDFs, then EDIT the [GENERATED] LaTeX so it looks
like [ORIGINAL] "same to same" — a reader placing the two side by side should
see the SAME design.

MATCH THE VISUAL DESIGN EXACTLY (this is the top priority):
- page size, margins/page density, and column layout,
- font family and the relative sizes of the name, section headings, job titles,
  and body text; bold/italic/small-caps/letter-spacing,
- colours (use \definecolor with the original's hex values),
- section-heading text case, rule/underline style and thickness, and the spacing
  above/below headings,
- bullet glyph, bullet indentation, and the line spacing within and between
  entries; date alignment; the header/contact block layout,
- overall spacing so the page is filled like the original.

MATCH THE SKILLS / TECHNICAL SKILLS SECTION CAREFULLY (common problem area):
- Reproduce the original's exact skills layout: bold category labels each on
  their own line (e.g. "Languages:", "Tools:") vs a single comma-separated list
  vs aligned columns — whatever the original uses.
- Fix spacing issues here: even row spacing, a consistent gap after each category
  label, nothing overflowing into the margin, and proper column alignment (use a
  tabular/`tabularx` or an aligned list, not scattered \hspace). Keep this
  section's line spacing tight and even like the rest of the resume.
- COLUMNS MUST NOT TOUCH: if columns or their text are colliding, add clear
  horizontal space between them — use a non-zero inter-column gap (a non-zero
  \tabcolsep, never zero, or an explicit small \hspace separator), remove any
  empty separator that strips the gap, leave a gap after each bold label so the
  value never butts against it, and give wrapping cells a fixed width so long
  values wrap inside their own column instead of bleeding into the next.
- ROWS MUST NOT TOUCH: never use negative row spacing (no `\\[-2pt]` /
  `\\[-3pt]` negative leading) between table rows — it makes lines collide. Use
  a plain double-backslash or an equal small positive gap on EVERY row so the
  rhythm is even, and do not squeeze the array-stretch value below 0.95. If some
  cells wrap to more lines than their row-mates, leaving uneven blank gaps,
  rebalance the content so cells in a row are similar height.

FIX OVERLAPPING HORIZONTAL LINES (common defect):
- If any rule/underline touches or crosses text — a section-heading rule cutting
  through the heading words, a rule colliding with the name or a line of content,
  or two rules stacking — separate them. Force the rule onto its own line with
  clear vertical space above and below it (a paragraph break plus a small
  vertical space before, and a small vertical space after) so it never overlaps
  any glyphs, matching the original's gap.

MATCH THE WRITING STYLE (rephrase the GENERATED text only — keep its facts):
- voice/person and verb tense for current vs past roles,
- bullet anatomy: same typical pattern, length, and number of bullets per role,
- the same use of strong/varied action verbs and metric phrasing,
- summary presence/length/tone, terminology level and formality,
- capitalisation and end-of-bullet punctuation habits.

FIT IT ON ONE PAGE (match the original's page count, normally ONE):
- The result MUST fit on a single page (unless [ORIGINAL] is clearly multi-page).
- Achieve this by tightening CONTENT only — keep the strongest, most relevant
  bullets, tighten wordy phrasing, and drop the weakest 1-2 bullets per role if
  needed. NEVER shrink fonts, change geometry/margins, or alter spacing macros
  to make it fit.
- Do not leave the page looking sparse either; fill it like the original.

HARD RULES:
- Do NOT copy any wording, employer, date, metric, or fact from [ORIGINAL] into
  [GENERATED]. Keep every fact from the GENERATED resume; only restyle HOW it is
  written and laid out.
- Keep the result compilable with pdflatex. Do not regress things that already
  match.

=== STYLE PROFILE OF THE ORIGINAL ===
{profile}
=== END STYLE PROFILE ===

=== CURRENT GENERATED LATEX SOURCE ===
{source}
=== END LATEX SOURCE ===

Output ONLY the complete, corrected LaTeX inside a single ```latex code block,
with no commentary before or after.
"""


class StyleMatcherAgent:
    """Agent that restyles the generated resume to match the original."""

    def __init__(self, client: ClaudeClient):
        self._client = client

    def match(
        self,
        *,
        resume_latex: str,
        style_profile: StyleProfile,
        original_pdf: Path,
        generated_pdf: Path,
    ) -> str:
        """Return restyled LaTeX whose style matches the original resume.

        Both PDFs are sent so the agent can compare the rendered output, and the
        style profile is provided as an explicit checklist.
        """
        prompt = _MATCHER_PROMPT.format(
            profile=style_profile.text if style_profile else "(none)",
            source=resume_latex,
        )
        response = self._client.ask(
            prompt,
            pdfs=[
                ("ORIGINAL", Path(original_pdf)),
                ("GENERATED", Path(generated_pdf)),
            ],
            system=_MATCHER_SYSTEM,
        )
        return extract_latex(response)


# --------------------------------------------------------------------------- #
# Agent 3: Replica Discriminator                                              #
# --------------------------------------------------------------------------- #


@dataclass
class DiscriminatorVerdict:
    """The Discriminator's judgement on a replica vs the original."""

    matches: bool
    feedback: str
    corrected_latex: str = ""

    @property
    def has_correction(self) -> bool:
        return bool(self.corrected_latex and self.corrected_latex.strip())


_DISCRIMINATOR_SYSTEM = (
    "You are a strict QA discriminator for resume replication. You compare an "
    "ORIGINAL resume against a REPLICA that is supposed to be visually identical "
    "to it (same content, same design). You judge whether the replication "
    "succeeded and, when it has not, you rewrite the replica's LaTeX so it "
    "matches the original — including the SAME number of pages."
)

_DISCRIMINATOR_PROMPT = r"""
Two PDFs are attached:
- [ORIGINAL] = the uploaded resume (the ground truth).
- [REPLICA]  = our LaTeX-built reproduction of it. Its LaTeX source is below.
{content_note}
The REPLICA is meant to be VISUALLY IDENTICAL to the ORIGINAL — same content,
same layout, and crucially the SAME NUMBER OF PAGES. Compare them carefully,
top to bottom.

Check for differences in:
- PAGE COUNT (most important): does the REPLICA have the same number of pages as
  the ORIGINAL? {page_note}
- margins / page density, font family and sizes (name, headings, body),
- colours, section-heading rule style and spacing,
- bullet glyph and indentation, date alignment, column layout,
- any missing, extra, or mis-placed element; spacing that is too loose or tight.

INSPECT THE SKILLS / TECHNICAL SKILLS SECTION CLOSELY (common problem area):
- Match the original's exact layout of skills: whether categories (e.g.
  "Languages:", "Frameworks:", "Tools:") sit on their OWN line with the label in
  bold, or whether skills are a single wrapped/comma-separated list, or arranged
  in aligned columns. Reproduce that structure exactly.
- Fix spacing problems here specifically: no awkward gaps between the category
  label and its values, consistent and even spacing between rows, no items
  overflowing into the margin, and proper alignment if the original uses columns
  (use a tabular/`tabularx` or aligned list rather than scattered \hspace).
- COLUMNS MUST NOT TOUCH: if adjacent columns or their text are colliding or have
  no gap between them, fix it by adding clear horizontal space — a non-zero
  inter-column gap (non-zero \tabcolsep, never zero, or an explicit small
  \hspace separator), removing any empty separator that strips the gap, and
  giving wrapping cells a fixed width so long values wrap inside their column
  instead of bleeding into the neighbouring column or the margin.
- ROWS MUST NOT TOUCH: flag and fix any negative row spacing (no `\\[-2pt]` /
  `\\[-3pt]` negative leading) between table rows — it makes lines collide. Rows
  must use a plain double-backslash or an equal small positive gap on every row,
  with the array-stretch value not squeezed below 0.95. If cells in a row wrap
  to very different line counts and leave uneven blank gaps, rebalance them.
- Keep the inter-line spacing in this section even and tight like the original —
  not looser than the rest of the resume.

CHECK FOR OVERLAPPING HORIZONTAL LINES (common defect):
- Look for any rule/underline that touches, crosses, or sits too close to text —
  e.g. a section-heading rule cutting through the heading words, a rule colliding
  with the name or the first line of a section, or two rules stacking onto each
  other. If found, fix it so the rule sits cleanly on its OWN line with clear
  vertical space above and below it (a paragraph break plus a small vertical
  space before, and a small vertical space after), never overlapping any glyphs.
  Match the original's gap exactly.

DECIDE:
- If the REPLICA already matches the ORIGINAL closely (same page count and the
  design is essentially indistinguishable), respond with exactly:
      VERDICT: MATCH
  followed by a one-line reason. Do NOT output any LaTeX in this case.

- Otherwise respond with:
      VERDICT: REVISE
  then a short bullet list of the concrete differences you found, then the
  COMPLETE corrected LaTeX that fixes them.

HOW TO FIX A PAGE-COUNT OVERFLOW (e.g. ORIGINAL is 1 page but REPLICA is 2):
- TIGHTEN THE LAYOUT to match the original's density: reduce \geometry margins,
  cut excessive \vspace / \titlespacing, reduce inter-item and inter-section
  spacing, and if needed nudge the base font size down to the original's
  (e.g. 11pt -> 10pt). Match the original's whitespace exactly.
- Do NOT delete the candidate's content to fit; keep ALL of the REPLICA's
  content. Fit it by adjusting spacing/margins/font to mirror the original.

Keep it compilable with pdflatex.

=== CURRENT REPLICA LATEX SOURCE ===
{source}
=== END LATEX SOURCE ===

When revising, output the corrected LaTeX inside a single ```latex code block
AFTER your verdict and bullet list.
"""


class ReplicaDiscriminatorAgent:
    """Agent that judges replica fidelity and corrects the LaTeX if needed."""

    def __init__(self, client: ClaudeClient):
        self._client = client

    def review(
        self,
        *,
        replica_latex: str,
        original_pdf: Path,
        replica_pdf: Path,
        replica_pages: Optional[int] = None,
        compare_format_only: bool = False,
    ) -> DiscriminatorVerdict:
        """Compare [ORIGINAL] vs [REPLICA] and return a verdict + any correction.

        ``replica_pages`` (the measured page count of the replica) is passed to
        the model as a hint so it doesn't have to guess the count. When
        ``compare_format_only`` is true, the REPLICA intentionally has DIFFERENT
        text (a tailored resume), so the agent compares ONLY the format/layout and
        must NOT copy the original's wording or facts.
        """
        page_note = (
            f"(We measured the REPLICA at {replica_pages} page(s).)"
            if replica_pages
            else ""
        )
        if compare_format_only:
            content_note = (
                "\nIMPORTANT: The [REPLICA] intentionally has DIFFERENT TEXT than "
                "[ORIGINAL] (it is a tailored resume). Judge and fix ONLY the "
                "FORMAT/LAYOUT/STYLE — margins, fonts, sizes, colours, heading "
                "rules, bullet style, spacing, alignment, columns, and page count. "
                "Do NOT copy any wording, bullet text, employer, date, or fact from "
                "[ORIGINAL] into the [REPLICA], and do NOT change the REPLICA's own "
                "text content. Keep all of the REPLICA's words exactly as they are; "
                "only restyle how they are laid out.\n"
            )
        else:
            content_note = (
                "\nThe [REPLICA] is meant to have the SAME content AND the same "
                "design as [ORIGINAL] — it should look identical.\n"
            )
        prompt = _DISCRIMINATOR_PROMPT.format(
            page_note=page_note, content_note=content_note, source=replica_latex
        )
        response = self._client.ask(
            prompt,
            pdfs=[
                ("ORIGINAL", Path(original_pdf)),
                ("REPLICA", Path(replica_pdf)),
            ],
            system=_DISCRIMINATOR_SYSTEM,
        )

        upper = response.upper()
        # A correction is only present when the model returned a fenced block.
        corrected = extract_latex(response) if "```" in response else ""
        # If a full document was returned without fences, treat it as a correction.
        if not corrected and "\\documentclass" in response:
            corrected = extract_latex(response)

        matched = "VERDICT: MATCH" in upper and not corrected
        feedback = response.strip()
        return DiscriminatorVerdict(
            matches=matched, feedback=feedback, corrected_latex=corrected
        )


_FIT_PAGES_SYSTEM = (
    "You are a LaTeX layout compression expert. You make a resume fit a target "
    "page count by tightening its DESIGN — margins, spacing, and font size — to "
    "match a reference's density, WITHOUT deleting or summarising any content. "
    "Every word, bullet, role, and section stays; you only change how tightly it "
    "is laid out."
)

_FIT_PAGES_PROMPT = r"""
The attached [ORIGINAL] PDF fits on {target} page(s). The LaTeX resume below is
a replica of it but currently compiles to {current} page(s) — too many. The
content is supposed to be identical to the original, so the overflow is caused
by the replica's layout being LOOSER than the original's.

Make the replica fit on exactly {target} page(s) by TIGHTENING THE DESIGN to
match the original's density. {aggression}

You MUST NOT delete, shorten, merge, or summarise any content — keep every
section, role, bullet, and word. Only change layout/spacing/sizing:
- reduce \geometry margins (top/bottom/left/right) to match the original,
- cut excessive vertical space: \vspace, \titlespacing above/below headings,
  \parskip, itemize \itemsep/\topsep/\parsep, and space between entries,
- tighten line spacing if the original is tighter,
- if still overflowing, reduce the base font size by one step (e.g. 11pt -> 10pt)
  to match the original.

Do NOT change colours, fonts family, headings wording, or the content. Keep it
compilable with pdflatex.

=== CURRENT REPLICA LATEX SOURCE ===
{source}
=== END LATEX SOURCE ===

Output ONLY the complete, corrected LaTeX inside a single ```latex code block,
with no commentary before or after.
"""


class PageFitterAgent:
    """Agent that tightens a resume's layout to hit a target page count."""

    def __init__(self, client: ClaudeClient):
        self._client = client

    def fit(
        self,
        *,
        replica_latex: str,
        original_pdf: Path,
        target_pages: int,
        current_pages: int,
        attempt: int = 0,
    ) -> str:
        """Return LaTeX tightened to fit ``target_pages`` (content preserved).

        ``attempt`` escalates how aggressively the layout is compressed.
        """
        if attempt == 0:
            aggression = (
                "Tighten moderately: trim the obvious excess vertical spacing and "
                "shrink margins to the original's."
            )
        elif attempt == 1:
            aggression = (
                "Tighten hard: minimise all inter-section and inter-item spacing, "
                "pull margins in further, and reduce line spacing to the original's."
            )
        else:
            aggression = (
                "Tighten aggressively: use tight margins, near-zero extra spacing, "
                "and drop the base font size one step if needed. Fitting on the "
                "target page count is required — but still keep ALL content."
            )
        prompt = _FIT_PAGES_PROMPT.format(
            target=target_pages,
            current=current_pages,
            aggression=aggression,
            source=replica_latex,
        )
        response = self._client.ask(
            prompt,
            pdf_path=Path(original_pdf),
            system=_FIT_PAGES_SYSTEM,
        )
        return extract_latex(response)


# --------------------------------------------------------------------------- #
# Coordinator                                                                  #
# --------------------------------------------------------------------------- #


class StyleAgentTeam:
    """Coordinates the Style Analyst and Style Matcher agents."""

    def __init__(self, client: ClaudeClient):
        self.analyst = StyleAnalystAgent(client)
        self.matcher = StyleMatcherAgent(client)
        self.discriminator = ReplicaDiscriminatorAgent(client)
        self.fitter = PageFitterAgent(client)

    def analyze(
        self,
        *,
        template_latex: str,
        original_pdf: Optional[Path] = None,
    ) -> StyleProfile:
        return self.analyst.analyze(
            template_latex=template_latex, original_pdf=original_pdf
        )

    def match(
        self,
        *,
        resume_latex: str,
        style_profile: StyleProfile,
        original_pdf: Path,
        generated_pdf: Path,
    ) -> str:
        return self.matcher.match(
            resume_latex=resume_latex,
            style_profile=style_profile,
            original_pdf=original_pdf,
            generated_pdf=generated_pdf,
        )

    def review(
        self,
        *,
        replica_latex: str,
        original_pdf: Path,
        replica_pdf: Path,
        replica_pages: Optional[int] = None,
        compare_format_only: bool = False,
    ) -> "DiscriminatorVerdict":
        return self.discriminator.review(
            replica_latex=replica_latex,
            original_pdf=original_pdf,
            replica_pdf=replica_pdf,
            replica_pages=replica_pages,
            compare_format_only=compare_format_only,
        )

    def fit_pages(
        self,
        *,
        replica_latex: str,
        original_pdf: Path,
        target_pages: int,
        current_pages: int,
        attempt: int = 0,
    ) -> str:
        return self.fitter.fit(
            replica_latex=replica_latex,
            original_pdf=original_pdf,
            target_pages=target_pages,
            current_pages=current_pages,
            attempt=attempt,
        )
