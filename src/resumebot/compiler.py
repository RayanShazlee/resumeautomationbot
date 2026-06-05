"""Step 3: Compile LaTeX source to PDF, with optional Claude-assisted repair."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .claude_client import ClaudeClient
from .textutils import extract_latex, freeze_preamble


class CompileError(RuntimeError):
    """Raised when LaTeX fails to compile after all attempts."""

    def __init__(self, message: str, log: str):
        super().__init__(message)
        self.log = log


def _page_count(log: str) -> Optional[int]:
    """Parse the page count from a pdflatex log (e.g. 'Output written ... (2 pages').

    LaTeX wraps long lines in the log at ~79 characters, so the
    "Output written on <long path> (N pages" message can be split across several
    lines. We collapse all whitespace before matching so the count is found
    regardless of wrapping.
    """
    flat = re.sub(r"\s+", " ", log)
    match = re.search(r"Output written on .*?\((\d+) pages?", flat)
    if match:
        return int(match.group(1))
    return None


def _run_pdflatex(pdflatex_path: str, tex_file: Path, workdir: Path) -> tuple[bool, str]:
    """Run pdflatex once; return (success, combined log/output).

    Note: we deliberately do NOT pass ``-halt-on-error``. Many LaTeX errors
    (e.g. an extra alignment tab, an overfull hbox) are recoverable — pdflatex
    can continue and still emit a usable PDF. Halting on the first error would
    discard that PDF. We treat the run as successful when a PDF is produced and
    the log contains no *fatal* error, regardless of the process exit code
    (pdflatex returns nonzero even for recoverable errors).
    """
    try:
        proc = subprocess.run(
            [
                pdflatex_path,
                "-interaction=nonstopmode",
                "-output-directory",
                str(workdir),
                str(tex_file),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise CompileError(
            f"pdflatex not found at '{pdflatex_path}'. Install TeX Live / MiKTeX "
            "or set PDFLATEX_PATH in your .env.",
            log="",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        return False, f"pdflatex timed out: {exc}"

    log_file = workdir / (tex_file.stem + ".log")
    log = log_file.read_text(errors="ignore") if log_file.exists() else ""
    output = proc.stdout + "\n" + proc.stderr + "\n" + log
    pdf_exists = (workdir / (tex_file.stem + ".pdf")).exists()
    # Success = a PDF was produced and no fatal (non-recoverable) error occurred.
    # pdflatex exits non-zero even for recoverable errors, so we don't gate on
    # the return code.
    pdf_ok = pdf_exists and not _has_fatal_error(log)
    return pdf_ok, output


_FATAL_PATTERNS = (
    "Emergency stop",
    "Fatal error occurred",
    "no output PDF file produced",
    "! LaTeX Error: File",          # missing .sty / package / class
    "! I can't find file",
    "Cannot determine size of graphic",
)


def _has_fatal_error(log: str) -> bool:
    """True if the log shows an error that prevents producing a usable PDF."""
    return any(pat in log for pat in _FATAL_PATTERNS)


def try_compile(
    latex_source: str,
    output_pdf: Path,
    pdflatex_path: str = "pdflatex",
) -> tuple[bool, str]:
    """Compile ``latex_source`` to ``output_pdf`` without raising or repairing.

    Returns ``(success, log)``. On success the PDF is written to ``output_pdf``.
    Used by the visual-refinement loop, which needs a persistent preview PDF.
    """
    output_pdf = Path(output_pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        tex_file = workdir / "resume.tex"
        tex_file.write_text(latex_source, encoding="utf-8")

        ok, log = _run_pdflatex(pdflatex_path, tex_file, workdir)
        if ok:
            ok, log = _run_pdflatex(pdflatex_path, tex_file, workdir)

        built_pdf = workdir / "resume.pdf"
        if built_pdf.exists():
            output_pdf.write_bytes(built_pdf.read_bytes())
            return ok, log
    return False, log


def compile_latex(
    latex_source: str,
    output_pdf: Path,
    pdflatex_path: str = "pdflatex",
    *,
    client: Optional[ClaudeClient] = None,
    max_fix_attempts: int = 2,
    enforce_one_page: bool = True,
    max_condense_attempts: int = 3,
) -> Path:
    """Compile ``latex_source`` to ``output_pdf``.

    Runs pdflatex twice (so cross-references settle). If compilation fails and a
    ``client`` is provided, asks Claude to repair the LaTeX and retries.

    If ``enforce_one_page`` is set and the resume compiles to more than one page,
    asks Claude to condense it to a single page (preserving the design) and
    recompiles, up to ``max_condense_attempts`` times.

    Returns the path to the generated PDF. Also writes the (possibly repaired)
    ``.tex`` source next to the PDF.
    """
    output_pdf = Path(output_pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    source = latex_source
    last_log = ""

    for attempt in range(max_fix_attempts + 1):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            tex_file = workdir / "resume.tex"
            tex_file.write_text(source, encoding="utf-8")

            ok, last_log = _run_pdflatex(pdflatex_path, tex_file, workdir)
            if ok:
                # Second pass for references/TOC.
                ok, last_log = _run_pdflatex(pdflatex_path, tex_file, workdir)

            built_pdf = workdir / "resume.pdf"
            if built_pdf.exists():
                # Enforce a single page if requested and possible.
                if client is not None and enforce_one_page:
                    source, last_log = _enforce_one_page(
                        client,
                        pdflatex_path,
                        source,
                        last_log,
                        workdir,
                        max_condense_attempts,
                    )
                    built_pdf = workdir / "resume.pdf"

                output_pdf.write_bytes(built_pdf.read_bytes())
                output_pdf.with_suffix(".tex").write_text(source, encoding="utf-8")
                return output_pdf

        # Compilation failed — try a Claude-assisted repair if possible.
        if client is None or attempt >= max_fix_attempts:
            break
        source = _repair_latex(client, source, last_log)

    # Save the failing source for debugging.
    output_pdf.with_suffix(".tex").write_text(source, encoding="utf-8")
    raise CompileError(
        "LaTeX compilation failed. See the .tex source and log for details.",
        log=_tail(last_log, 60),
    )


def _enforce_one_page(
    client: ClaudeClient,
    pdflatex_path: str,
    source: str,
    log: str,
    workdir: Path,
    max_attempts: int,
) -> tuple[str, str]:
    """If the document is >1 page, ask Claude to condense and recompile.

    Returns the (source, log) of the best result. The compiled PDF is left in
    ``workdir`` as resume.pdf.
    """
    for attempt in range(max_attempts):
        pages = _page_count(log)
        if pages is None or pages <= 1:
            return source, log

        condensed = _condense_to_one_page(client, source, pages, attempt, max_attempts)
        tex_file = workdir / "resume.tex"
        tex_file.write_text(condensed, encoding="utf-8")

        ok, new_log = _run_pdflatex(pdflatex_path, tex_file, workdir)
        if ok:
            ok, new_log = _run_pdflatex(pdflatex_path, tex_file, workdir)

        if (workdir / "resume.pdf").exists() and ok:
            # Accept the condensed version only if it still compiles.
            source, log = condensed, new_log
        else:
            # Condensed version broke compilation; keep the previous source and
            # rebuild it so the PDF in workdir matches `source`.
            tex_file.write_text(source, encoding="utf-8")
            _run_pdflatex(pdflatex_path, tex_file, workdir)
            _run_pdflatex(pdflatex_path, tex_file, workdir)
            break

    return source, log


def _condense_to_one_page(
    client: ClaudeClient,
    source: str,
    pages: int,
    attempt: int = 0,
    max_attempts: int = 3,
) -> str:
    # Escalate how aggressively we trim on each successive attempt.
    if attempt == 0:
        aggression = (
            "Trim lightly: remove the 1-2 weakest bullets per role and tighten "
            "wordy phrasing so each bullet fits on one line."
        )
    elif attempt == 1:
        aggression = (
            "Trim harder: keep only the 3-4 strongest, most JD-relevant bullets "
            "per role, shorten the summary to 2 lines, and condense skills lists."
        )
    else:
        aggression = (
            "Trim aggressively: keep only the 2-3 most relevant roles and the 2-3 "
            "strongest bullets each, cut the oldest/least relevant roles entirely, "
            "and reduce the summary to a single tight sentence. Getting it onto ONE "
            "page is more important than retaining every detail."
        )
    prompt = (
        f"This LaTeX resume currently compiles to {pages} pages, but it MUST fit "
        "on exactly ONE page. Condense it to a single page by EDITING CONTENT "
        f"ONLY. {aggression}\n\n"
        "Do NOT touch the preamble (everything before \\begin{document}); do NOT "
        "change fonts, geometry, margins, colours, rules, or any spacing macros. "
        "Keep the strongest, most relevant content and the exact same design. "
        "Never invent facts.\n\n"
        "=== LATEX SOURCE ===\n"
        f"{source}\n\n"
        "Output ONLY the corrected one-page LaTeX inside a single ```latex code block."
    )
    response = client.ask(prompt)
    condensed = extract_latex(response)
    # Lock the original formatting: keep the source preamble, take only the body.
    return freeze_preamble(source, condensed)


def _repair_latex(client: ClaudeClient, source: str, log: str) -> str:
    errors = _extract_error_lines(log)
    prompt = (
        "The following LaTeX resume failed to compile cleanly with pdflatex. Fix "
        "ALL errors and return the COMPLETE corrected document. Keep the design "
        "identical; only fix what prevents a clean compile.\n\n"
        "Common causes to check: a tabular/tabularx row with too many '&' columns "
        "for its column spec (\"Extra alignment tab\"), unescaped special "
        "characters (& % $ # _ { } ~ ^), a missing package, an undefined command, "
        "or unbalanced braces/environments.\n\n"
        "=== KEY ERROR LINES ===\n"
        f"{errors}\n\n"
        "=== FULL ERROR LOG (tail) ===\n"
        f"{_tail(log, 80)}\n\n"
        "=== LATEX SOURCE ===\n"
        f"{source}\n\n"
        "Output ONLY the corrected LaTeX inside a single ```latex code block."
    )
    response = client.ask(prompt)
    return extract_latex(response)


def _extract_error_lines(log: str) -> str:
    """Pull the most informative error/warning lines out of a pdflatex log."""
    keep = []
    for line in log.splitlines():
        stripped = line.strip()
        if stripped.startswith("!") or "Error" in stripped or "Undefined" in stripped:
            keep.append(stripped)
    return "\n".join(keep[-25:]) if keep else "(no explicit error lines found)"


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])
