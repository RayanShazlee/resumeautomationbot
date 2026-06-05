"""Step 1: Convert a target resume PDF into a reusable LaTeX template.

Claude reads the PDF natively and reproduces its visual style (fonts, spacing,
section headers, bullet style, columns, rules, colours) as a clean, compilable
LaTeX document that we can later refill with new content.
"""

from __future__ import annotations

from pathlib import Path

from .claude_client import ClaudeClient
from .textutils import extract_latex

_SYSTEM = (
    "You are a world-class LaTeX typesetter and document-forensics expert. You "
    "reverse-engineer a resume PDF into LaTeX that is visually INDISTINGUISHABLE "
    "from the original — pixel-accurate margins, fonts, sizes, colours, rules, "
    "and spacing. You measure carefully and never approximate when you can match."
)

_PROMPT = r"""
The attached PDF is a resume. Reproduce it as a single, self-contained,
compilable LaTeX document that is VISUALLY IDENTICAL to the original — a reader
placing the two PDFs side by side should not be able to tell them apart.

Work like a forensic analyst. Before writing LaTeX, study the PDF and match:

PAGE & GEOMETRY
- Page size (Letter vs A4) and EXACT margins. Estimate them in inches/cm from
  the whitespace and set them with \usepackage[margin=...]{geometry} (or
  per-side \geometry{top=,bottom=,left=,right=}). Resumes are usually tight
  (0.4in–0.6in). Match the original's density.
- Single vs two-column layout. If two columns (e.g. a sidebar), reproduce the
  column widths and the divider exactly (paracol/minipage as appropriate).
- The number of pages MUST match the original (usually one page).

TYPOGRAPHY
- Identify the font family family (serif vs sans-serif) and approximate it with
  the closest common LaTeX font package (e.g. sans: \usepackage{helvet} +
  \renewcommand\familydefault{\sfdefault}, or carlito/lato; serif: default CM,
  times, or charter). Add a comment naming the original font if you can.
- Match the base font size (10pt/11pt/12pt) AND the relative sizes of the name,
  section headings, job titles, and body text. Reproduce bold/italic/small-caps
  and letter-spacing used in headings.
- Match any colours EXACTLY (define them with \definecolor using hex values you
  read from the PDF — name colour, heading colour, rule colour, link colour).

STRUCTURE & DECORATION
- Reproduce the header/contact block layout precisely: name placement
  (centered/left), how contact items are separated (• | / line breaks), and any
  icons (use fontawesome5 if icons are present, else plain text).
- Match section headings exactly: text case, the rule/underline style and
  thickness (\titlerule, \hrule height), spacing above/below, and indentation.
- Match bullet style (•, –, ▪), bullet indentation, and the line spacing inside
  and between entries. Reproduce date alignment (right-aligned dates via \hfill
  or tabular).

DO NOT INVENT DECORATIONS (critical):
- Add a horizontal rule / line ONLY where one is actually visible in the PDF.
  Do NOT add any rule that is not in the original. In particular, do NOT place a
  horizontal line between the contact/header block and the first section
  (e.g. Profile Summary) unless the PDF clearly shows one there.
- Likewise do not add boxes, borders, background shading, or separator lines
  that the original does not have. When in doubt, leave it out.

ROBUSTNESS
- Use only packages in a standard TeX Live / MiKTeX install. It MUST compile with
  pdflatex on the first run.
- Define reusable macros for repeated elements (\resumeentry for a job/edu entry,
  a bullet list environment, and a \section-style command) so the template is
  trivial to refill later WITHOUT changing the look.
- Keep the SAME example content/text that appears in the PDF for now (we replace
  it later). Preserve the exact section order.

Output ONLY the complete LaTeX source inside a single ```latex code block, with
no commentary before or after.
"""


_REFINE_SYSTEM = (
    "You are a meticulous LaTeX typesetter performing a visual diff between an "
    "ORIGINAL resume PDF and a GENERATED one built from LaTeX. You adjust the "
    "LaTeX so the generated output matches the original as closely as possible."
)

_REFINE_PROMPT = r"""
Two PDFs are attached:
- [ORIGINAL] = the target design we must match.
- [GENERATED] = the current output of the LaTeX source below.

Compare them carefully, top to bottom, and list (mentally) every visual
difference: margins/page density, font family or size, name/heading sizes,
colours, section-heading rule style/thickness, spacing between sections and
bullets, bullet glyphs and indentation, date alignment, column widths, and any
element that is missing, extra, or mis-placed.

Then EDIT the LaTeX so the GENERATED output looks like the ORIGINAL:
- Adjust \geometry margins to match the original's whitespace/density.
- Fix the font package/size and the relative sizes of name/headings/body.
- Correct colours via \definecolor to the original's hex values.
- Match heading rules, spacing (\titlespacing, \vspace), bullet style, and
  date alignment.
- If content overflows or underflows the page, tune spacing so the page fill
  matches the original (same number of pages).
- REMOVE any horizontal rule / line / box / shading that appears in GENERATED
  but NOT in ORIGINAL. Especially: if GENERATED has a horizontal line between the
  contact/header block and the first section (e.g. Profile Summary) that the
  ORIGINAL does not have, delete it. Never add a rule the original lacks.

Keep the document compilable with pdflatex and keep the same example content.
Do NOT regress things that already match.

=== CURRENT LATEX SOURCE ===
{source}
=== END LATEX SOURCE ===

Output ONLY the complete, corrected LaTeX inside a single ```latex code block,
with no commentary before or after.
"""


def pdf_to_latex_template(client: ClaudeClient, pdf_path: Path) -> str:
    """Return LaTeX source that reproduces the target resume's design."""
    response = client.ask(_PROMPT, pdf_path=Path(pdf_path), system=_SYSTEM)
    return extract_latex(response)


def refine_template_against_original(
    client: ClaudeClient,
    *,
    template_latex: str,
    original_pdf: Path,
    generated_pdf: Path,
) -> str:
    """Ask Claude to adjust the LaTeX so [GENERATED] matches [ORIGINAL].

    Both PDFs are sent so Claude can do a true visual comparison. Returns the
    corrected LaTeX source.
    """
    prompt = _REFINE_PROMPT.format(source=template_latex)
    response = client.ask(
        prompt,
        pdfs=[("ORIGINAL", Path(original_pdf)), ("GENERATED", Path(generated_pdf))],
        system=_REFINE_SYSTEM,
    )
    return extract_latex(response)


_FORMAT_AUDIT_PROMPT = r"""
Two PDFs are attached:
- [ORIGINAL] = the reference design whose FORMAT/STYLE must be matched.
- [GENERATED] = the candidate's tailored resume (DIFFERENT text content).

The two intentionally have DIFFERENT wording — do NOT copy text from [ORIGINAL]
into [GENERATED]. Compare ONLY the visual formatting and fix the GENERATED
resume's LaTeX so its STYLE matches [ORIGINAL]:
- margins / page density, font family and sizes (name, headings, body),
- colours, section-heading rule style and spacing,
- bullet glyph and indentation, date alignment, column layout,
- overall spacing so the page is filled like the original (same page count).

Keep ALL of the candidate's current text content exactly as it is in the LaTeX
below. Only change styling/layout/spacing. Keep it compilable with pdflatex.

=== CURRENT LATEX SOURCE (candidate's resume) ===
{source}
=== END LATEX SOURCE ===

Output ONLY the complete, corrected LaTeX inside a single ```latex code block,
with no commentary before or after.
"""


def refine_resume_format(
    client: ClaudeClient,
    *,
    resume_latex: str,
    original_pdf: Path,
    generated_pdf: Path,
) -> str:
    """Fix the final resume's STYLING to match the original (keeps its content)."""
    prompt = _FORMAT_AUDIT_PROMPT.format(source=resume_latex)
    response = client.ask(
        prompt,
        pdfs=[("ORIGINAL", Path(original_pdf)), ("GENERATED", Path(generated_pdf))],
        system=_REFINE_SYSTEM,
    )
    return extract_latex(response)


