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
- HORIZONTAL RULES MUST NOT OVERLAP TEXT: when a section heading has an
  underline/rule, put the rule on its OWN line with clear vertical space, e.g.
  `\nointerlineskip` is NOT enough — use a small \vspace before the rule and the
  text resumes below it. Prefer `{\color{rulecolor}\titlerule[0.8pt]}` or
  `\par\vspace{2pt}\hrule height 0.8pt\vspace{4pt}` so the line sits cleanly
  between the heading and the body and never crosses the heading text, the name,
  or the first line of content. Give every rule non-zero vertical breathing room
  above and below.
- Match bullet style (•, –, ▪), bullet indentation, and the line spacing inside
  and between entries. Reproduce date alignment (right-aligned dates via \hfill
  or tabular).

SKILLS / TECHNICAL SKILLS SECTION (reproduce its structure precisely):
- Detect how skills are laid out in the PDF and mirror it EXACTLY: bold category
  labels each on their own line (e.g. "Languages:", "Frameworks:", "Tools:")
  followed by their values; OR a single comma-separated wrapped list; OR two/
  three aligned columns. Do not flatten a categorised layout into one list.
- Use a clean, robust construct for it: an aligned `tabular`/`tabularx` for
  column or label:value layouts, or a tight list — NOT scattered \hspace or
  manual spaces. Ensure even spacing between rows, a consistent gap after each
  label, no items running into the margin, and spacing as tight as the original.
- COLUMN SPACING (critical — columns MUST NOT touch): never let adjacent columns
  or their text collide. Do NOT use empty `@{}` separators between columns that
  hold text. Leave a clear horizontal gap between columns, e.g. add a small
  fixed gap with `@{\hspace{1.5em}}` between columns, or use `tabularx` with `X`
  columns and `\setlength{\tabcolsep}{8pt}` (never 0pt). For a label:value row,
  put a gap after the label (e.g. a `p{..}` label column plus column separation)
  so the value never butts up against the bold label. Verify visually that there
  is whitespace between every pair of columns.
- For wrapped value lists, give each cell a fixed width (`p{}` / `X`) so long
  values wrap INSIDE the column instead of overflowing into the next column or
  the margin.
- ROW SPACING (critical — rows MUST NOT touch): NEVER use negative row spacing
  such as `\\[-2pt]` or `\\[-3pt]` between table rows — negative leading makes
  the lines collide and descenders overlap the row below. Use a plain `\\` or a
  small POSITIVE gap like `\\[2pt]` and keep that SAME value on every row so the
  vertical rhythm is even. Do NOT squeeze `\arraystretch` below `0.95` (use
  `1.0` for multi-line cells). When some cells in a multi-column skills table
  wrap to 2–3 lines while their row-mates are 1 line, balance the content (split
  long categories, move items) so cells in a row have similar height — this
  keeps the gaps regular instead of leaving big blank holes in the short cells.

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
- SKILLS / TECHNICAL SKILLS SECTION: check this area closely. Reproduce the
  original's exact skills layout (bold category labels on their own lines, a
  single comma-separated list, or aligned columns) and fix any spacing problems —
  even row spacing, a consistent gap after each label, proper column alignment
  (use a tabular/tabularx, not scattered \hspace), and nothing spilling into the
  margin.
- COLUMNS MUST NOT TOUCH in the skills/technical section: if adjacent columns or
  their text are colliding, add clear horizontal space between them — increase
  the inter-column gap (a non-zero \tabcolsep, never zero; or an explicit small
  \hspace separator between columns), remove any empty separator that strips the
  gap, and give wrapping cells a fixed width so long values wrap inside their own
  column instead of running into the next one.
- ROWS MUST NOT TOUCH: never use negative row spacing (no `\\[-2pt]` or
  `\\[-3pt]` style negative leading) between rows — it makes lines collide. Use
  a plain double-backslash or an equal small positive gap on EVERY row so the
  spacing is even. Do not squeeze the array-stretch value below 0.95. If some
  cells wrap to more lines than their row-mates and leave big uneven blank gaps,
  rebalance the content so cells in a row are similar height and the gaps stay
  regular.
- FIX OVERLAPPING HORIZONTAL LINES: if any rule/underline in GENERATED touches or
  crosses text (the name, a section heading, or a line of content), separate them
  by forcing the rule onto its own line with vertical space around it
  (\par\vspace before, \vspace after). A heading rule must sit cleanly BELOW the
  heading text, not through it; never let two rules or a rule and text collide.

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


