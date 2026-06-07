"""High-level pipeline tying the steps together (used by CLI and web app)."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .claude_client import ClaudeClient
from .compiler import compile_latex, try_compile
from .compiler import _page_count as _pdf_page_count
from .config import Config
from .agents import StyleAgentTeam, StyleProfile
from .generator import generate_resume
from .latex_convert import (
    pdf_to_latex_template,
    refine_template_against_original,
)


def _count_pdf_pages(pdf_path: Path) -> Optional[int]:
    """Count the pages of an existing PDF file.

    Tries ``pdfinfo`` (poppler) first, then falls back to a lightweight scan of
    the raw PDF for page objects. Returns ``None`` if the count can't be found.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return None

    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        try:
            proc = subprocess.run(
                [pdfinfo, str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            match = re.search(r"^Pages:\s+(\d+)", proc.stdout, re.MULTILINE)
            if match:
                return int(match.group(1))
        except Exception:  # noqa: BLE001 - fall back to byte scan
            pass

    # Fallback: scan the raw bytes for page objects.
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return None
    # Prefer the /Count on the page tree root if present.
    counts = [int(m) for m in re.findall(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", data)]
    if counts:
        return max(counts)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
    return pages or None


@dataclass
class PipelineResult:
    template_latex: str
    resume_latex: str
    pdf_path: Optional[Path]
    tex_path: Path
    compiled: bool
    compile_error: Optional[str] = None
    style_profile: Optional[str] = None


@dataclass
class ReplicationResult:
    """Outcome of replicating an uploaded resume's design + content as LaTeX."""

    template_latex: str
    pdf_path: Optional[Path]
    tex_path: Path
    compiled: bool
    compile_error: Optional[str] = None
    style_profile: Optional[str] = None
    discriminator_feedback: Optional[str] = None
    replica_pages: Optional[int] = None


def build_template(config: Config, target_pdf: Path, *, refine_rounds: int = 2) -> str:
    """Step 1 only: turn a target resume PDF into a LaTeX template.

    Runs the visual-refinement loop so the template matches the original PDF.
    """
    client = ClaudeClient(config)
    return _build_refined_template(
        client, config, Path(target_pdf), refine_rounds=refine_rounds
    )


def replicate_resume(
    config: Config,
    target_pdf: Path,
    *,
    output_basename: str = "replica",
    refine_rounds: int = 2,
    analyze_style: bool = True,
    discriminator_rounds: int = 2,
) -> ReplicationResult:
    """Replicate an uploaded resume as LaTeX and compile it to a PDF.

    Unlike :func:`run_pipeline` (which tailors NEW content to a job), this simply
    reproduces the uploaded resume "same to same": the multi-agent system rebuilds
    the design as LaTeX and visually refines it against the original PDF (the
    Style Analyst additionally describes the style), then compiles a replica PDF
    you can compare against the original side by side. The replica keeps the
    original's own example content so the two should look identical.

    Finally a Discriminator agent compares the compiled replica against the
    original (up to ``discriminator_rounds`` times) and, when they don't match
    (e.g. the replica spilled onto a second page), rewrites the LaTeX to fix it —
    tightening the design to match the original's density rather than cutting
    content. Only corrections that still compile are accepted.
    """
    client = ClaudeClient(config)
    team = StyleAgentTeam(client)

    # Agents replicate the design: reconstruct LaTeX + visually refine vs original.
    template_latex = _build_refined_template(
        client, config, Path(target_pdf), refine_rounds=refine_rounds
    )

    # Style Analyst: describe the style that was replicated (for transparency).
    style_profile: StyleProfile = StyleProfile(text="")
    if analyze_style:
        style_profile = team.analyze(
            template_latex=template_latex, original_pdf=Path(target_pdf)
        )

    from .config import OUTPUT_DIR

    pdf_path = OUTPUT_DIR / f"{output_basename}.pdf"
    tex_path = OUTPUT_DIR / f"{output_basename}.tex"

    compiled = False
    compile_error: Optional[str] = None
    discriminator_feedback: Optional[str] = None
    replica_pages: Optional[int] = None

    try:
        compile_latex(
            template_latex,
            pdf_path,
            pdflatex_path=config.pdflatex_path,
            client=client,
            # Replicate the original exactly — never trim content to one page.
            enforce_one_page=False,
        )
        compiled = True
        template_latex = pdf_path.with_suffix(".tex").read_text(encoding="utf-8")
        tex_path = pdf_path.with_suffix(".tex")

        # Make the replica match the original exactly: deterministic page-fit
        # (measured) + Discriminator review + final page guard.
        original_pages = _count_pdf_pages(Path(target_pdf))
        template_latex, discriminator_feedback, replica_pages = (
            _enforce_replica_fidelity(
                team,
                config,
                latex=template_latex,
                original_pdf=Path(target_pdf),
                work_pdf=pdf_path,
                original_pages=original_pages,
                discriminator_rounds=discriminator_rounds,
            )
        )
        tex_path.write_text(template_latex, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - surface message to caller
        compile_error = str(exc)
        tex_path.write_text(template_latex, encoding="utf-8")
        pdf_path = None  # type: ignore[assignment]

    return ReplicationResult(
        template_latex=template_latex,
        pdf_path=pdf_path,
        tex_path=tex_path,
        compiled=compiled,
        compile_error=compile_error,
        style_profile=style_profile.text or None,
        discriminator_feedback=discriminator_feedback,
        replica_pages=replica_pages,
    )


def _discriminate_replica(
    team: StyleAgentTeam,
    config: Config,
    *,
    replica_latex: str,
    original_pdf: Path,
    replica_pdf: Path,
    rounds: int,
    compare_format_only: bool = False,
) -> tuple[str, Optional[str], Optional[int]]:
    """Run the Discriminator agent, keeping only corrections that still compile.

    Returns ``(latex, last_feedback, page_count)``. Each round measures the
    replica's page count, asks the Discriminator to compare it against the
    original, and applies its correction if the model flags a mismatch and the
    corrected LaTeX still compiles. Stops early when the Discriminator says the
    replica matches. When ``compare_format_only`` is true, the replica has
    different (tailored) text and only the FORMAT is compared.
    """
    feedback: Optional[str] = None
    pages = _page_count_of(replica_latex, replica_pdf, config)

    for _ in range(max(0, rounds)):
        verdict = team.review(
            replica_latex=replica_latex,
            original_pdf=original_pdf,
            replica_pdf=replica_pdf,
            replica_pages=pages,
            compare_format_only=compare_format_only,
        )
        feedback = verdict.feedback
        if verdict.matches or not verdict.has_correction:
            break

        corrected = verdict.corrected_latex
        if corrected.strip() == replica_latex.strip():
            break

        ok, log = try_compile(
            corrected, replica_pdf, pdflatex_path=config.pdflatex_path
        )
        if ok:
            replica_latex = corrected
            pages = _pdf_page_count(log) or pages
        else:
            # Correction broke compilation; restore the last good replica & stop.
            try_compile(
                replica_latex, replica_pdf, pdflatex_path=config.pdflatex_path
            )
            break

    return replica_latex, feedback, pages


def _page_count_of(
    latex_source: str, pdf_path: Path, config: Config
) -> Optional[int]:
    """Compile ``latex_source`` to refresh ``pdf_path`` and return its page count."""
    ok, log = try_compile(latex_source, pdf_path, pdflatex_path=config.pdflatex_path)
    if ok:
        return _pdf_page_count(log)
    return None


def _fit_replica_to_pages(
    team: StyleAgentTeam,
    config: Config,
    *,
    replica_latex: str,
    original_pdf: Path,
    replica_pdf: Path,
    target_pages: Optional[int],
    max_attempts: int,
) -> tuple[str, Optional[int]]:
    """Tighten the replica's DESIGN until it fits ``target_pages`` (measured).

    This is deterministic: each round measures the replica's real page count and,
    if it still exceeds the target, asks the Page Fitter agent to compress the
    layout (margins/spacing/font, never content) with escalating aggression. Only
    versions that compile AND do not increase the page count are accepted.
    Returns ``(latex, measured_pages)``.
    """
    current = _page_count_of(replica_latex, replica_pdf, config)
    if not target_pages or current is None:
        return replica_latex, current

    attempt = 0
    while current > target_pages and attempt < max_attempts:
        candidate = team.fit_pages(
            replica_latex=replica_latex,
            original_pdf=original_pdf,
            target_pages=target_pages,
            current_pages=current,
            attempt=attempt,
        )
        attempt += 1
        if not candidate.strip() or candidate.strip() == replica_latex.strip():
            continue
        ok, log = try_compile(
            candidate, replica_pdf, pdflatex_path=config.pdflatex_path
        )
        if not ok:
            # Broke compilation; restore the last good replica and try again with
            # more aggression on the next loop.
            try_compile(replica_latex, replica_pdf, pdflatex_path=config.pdflatex_path)
            continue
        new_pages = _pdf_page_count(log)
        if new_pages is None:
            continue
        # Accept only if it didn't make things worse.
        if new_pages <= current:
            replica_latex, current = candidate, new_pages
        else:
            try_compile(replica_latex, replica_pdf, pdflatex_path=config.pdflatex_path)

    # Make sure the PDF on disk reflects the chosen source.
    try_compile(replica_latex, replica_pdf, pdflatex_path=config.pdflatex_path)
    return replica_latex, current


def _enforce_replica_fidelity(
    team: StyleAgentTeam,
    config: Config,
    *,
    latex: str,
    original_pdf: Path,
    work_pdf: Path,
    original_pages: Optional[int],
    discriminator_rounds: int,
    fit_attempts: int = 4,
    compare_format_only: bool = False,
) -> tuple[str, Optional[str], Optional[int]]:
    """Make ``latex`` match ``original_pdf`` exactly: page-fit + discriminator.

    Shared by replication and the JD-tailoring flow. Tightens the design to the
    original's page count, runs the Discriminator to fix remaining visual diffs,
    then re-fits once more if the discriminator's edits re-inflated the page
    count. Returns ``(latex, discriminator_feedback, measured_pages)``. When
    ``compare_format_only`` is true (tailoring flow), the Discriminator compares
    only the FORMAT and never copies the original's text.
    """
    latex, pages = _fit_replica_to_pages(
        team,
        config,
        replica_latex=latex,
        original_pdf=original_pdf,
        replica_pdf=work_pdf,
        target_pages=original_pages,
        max_attempts=fit_attempts,
    )

    latex, feedback, dpages = _discriminate_replica(
        team,
        config,
        replica_latex=latex,
        original_pdf=original_pdf,
        replica_pdf=work_pdf,
        rounds=discriminator_rounds,
        compare_format_only=compare_format_only,
    )
    pages = dpages or pages

    # Final guard: if the discriminator's edits pushed it back over the target
    # page count, tighten once more so the result truly matches.
    if original_pages and pages and pages > original_pages:
        latex, pages = _fit_replica_to_pages(
            team,
            config,
            replica_latex=latex,
            original_pdf=original_pdf,
            replica_pdf=work_pdf,
            target_pages=original_pages,
            max_attempts=2,
        )

    return latex, feedback, pages


def _build_refined_template(
    client: ClaudeClient,
    config: Config,
    target_pdf: Path,
    *,
    refine_rounds: int,
) -> str:
    """Create a LaTeX template from a PDF and visually refine it to match.

    1. Claude reconstructs the design as LaTeX.
    2. We compile it to a preview PDF.
    3. Claude compares [ORIGINAL] vs [GENERATED] and corrects the LaTeX.
    4. Repeat step 2-3 ``refine_rounds`` times, keeping only versions that still
       compile.
    """
    from .config import OUTPUT_DIR

    template = pdf_to_latex_template(client, target_pdf)

    if refine_rounds <= 0:
        return template

    preview_pdf = OUTPUT_DIR / "_template_preview.pdf"
    ok, _ = try_compile(template, preview_pdf, pdflatex_path=config.pdflatex_path)

    for _ in range(refine_rounds):
        if not ok or not preview_pdf.exists():
            break  # can't compare without a rendered preview
        refined = refine_template_against_original(
            client,
            template_latex=template,
            original_pdf=target_pdf,
            generated_pdf=preview_pdf,
        )
        new_ok, _ = try_compile(refined, preview_pdf, pdflatex_path=config.pdflatex_path)
        if new_ok:
            template, ok = refined, True
        else:
            # Refinement broke compilation; keep the last good template and stop.
            try_compile(template, preview_pdf, pdflatex_path=config.pdflatex_path)
            break

    return template


def run_pipeline(
    config: Config,
    *,
    target_pdf: Optional[Path],
    template_latex: Optional[str],
    job_description: str,
    details: str,
    sections: Optional[Sequence[str]] = None,
    instructions: str = "",
    output_basename: str = "resume",
    compile_pdf: bool = True,
    refine_rounds: int = 2,
    match_style: bool = True,
    style_match_rounds: int = 1,
    enforce_one_page: bool = True,
    discriminator_rounds: int = 2,
) -> PipelineResult:
    """Run the full flow.

    Either ``target_pdf`` (to derive a template) or a pre-built ``template_latex``
    must be supplied. ``sections`` optionally limits which resume sections are
    tailored to the JD (the rest stay as the original). ``refine_rounds``
    controls how many visual-comparison passes are used to make the template
    match the original PDF.

    When ``target_pdf`` is supplied, the bot FIRST replicates the uploaded resume
    with the full multi-agent pipeline (reconstruct → visually refine → measured
    page-fit → Discriminator review) so the base template matches the original
    exactly — same design and same page count — BEFORE any tailoring. Then it
    fills in JD-tailored content on that faithful base.

    When ``match_style`` is true, a two-agent style team is used: a Style Analyst
    agent inspects the uploaded resume and produces a style profile that guides
    content generation, and a Style Matcher agent then compares the generated
    resume against the original (``style_match_rounds`` times) and restyles it to
    match "same to same" without copying any facts.

    When ``enforce_one_page`` is true (default), the result is condensed to fit on
    a single page after generation; when a ``target_pdf`` is given the tailored
    resume is additionally fitted to the ORIGINAL's page count by tightening the
    design (never cutting content). Returns a :class:`PipelineResult`.
    """
    if not template_latex and not target_pdf:
        raise ValueError("Provide either target_pdf or template_latex.")

    client = ClaudeClient(config)
    team = StyleAgentTeam(client)

    from .config import OUTPUT_DIR

    original_pages: Optional[int] = None

    if not template_latex:
        # Phase 1 — REPLICATE: build a faithful template from the uploaded resume
        # using the full agent pipeline, so tailoring starts from an exact copy.
        assert target_pdf is not None  # guaranteed by the validation above
        src_pdf = Path(target_pdf)
        base_pdf = OUTPUT_DIR / f"{output_basename}_base.pdf"
        template_latex = _build_refined_template(
            client, config, src_pdf, refine_rounds=refine_rounds
        )
        original_pages = _count_pdf_pages(src_pdf)
        try:
            compile_latex(
                template_latex,
                base_pdf,
                pdflatex_path=config.pdflatex_path,
                client=client,
                enforce_one_page=False,
            )
            template_latex = base_pdf.with_suffix(".tex").read_text(encoding="utf-8")
            template_latex, _, original_pages_measured = _enforce_replica_fidelity(
                team,
                config,
                latex=template_latex,
                original_pdf=src_pdf,
                work_pdf=base_pdf,
                original_pages=original_pages,
                discriminator_rounds=discriminator_rounds,
            )
            original_pages = original_pages or original_pages_measured
        except Exception:  # noqa: BLE001 - fall back to the unrefined template
            pass

    # Agent 1 — Style Analyst: read the uploaded resume and describe its style so
    # the writer can reproduce it. Needs the template (always) and the original
    # PDF when available for a true read of the rendered design.
    style_profile: StyleProfile = StyleProfile(text="")
    if match_style:
        style_profile = team.analyze(
            template_latex=template_latex,
            original_pdf=Path(target_pdf) if target_pdf else None,
        )

    resume_latex = generate_resume(
        client,
        template_latex=template_latex,
        job_description=job_description,
        details=details,
        sections=sections,
        instructions=instructions,
        style_profile=style_profile.text,
    )

    pdf_path = OUTPUT_DIR / f"{output_basename}.pdf"
    tex_path = OUTPUT_DIR / f"{output_basename}.tex"

    compiled = False
    compile_error: Optional[str] = None

    # When we have the original PDF, page matching is done by TIGHTENING THE
    # DESIGN (like the replica flow), never by condensing/removing content. Only
    # fall back to content-condensing one-page enforcement when there's no
    # original to match against.
    condense_one_page = enforce_one_page and not target_pdf

    if compile_pdf:
        try:
            # First compile: fix any errors. Do NOT remove content to fit a page
            # when we have an original — the design-fit step handles page count.
            compile_latex(
                resume_latex,
                pdf_path,
                pdflatex_path=config.pdflatex_path,
                client=client,
                enforce_one_page=condense_one_page,
            )
            compiled = True
            # compile_latex rewrites the .tex (possibly repaired); reload it.
            resume_latex = pdf_path.with_suffix(".tex").read_text(encoding="utf-8")
            tex_path = pdf_path.with_suffix(".tex")

            # Agent 2 — Style Matcher: compare the generated resume against the
            # original and restyle it to match the format 100%. Needs both PDFs.
            if match_style and target_pdf and style_profile:
                restyled = _match_style_rounds(
                    team,
                    config,
                    resume_latex=resume_latex,
                    style_profile=style_profile,
                    original_pdf=Path(target_pdf),
                    generated_pdf=pdf_path,
                    rounds=style_match_rounds,
                )
                if restyled.strip() and restyled.strip() != resume_latex.strip():
                    # Re-compile the restyled version (no content condensing when
                    # we have an original — design-fit handles the page count).
                    compile_latex(
                        restyled,
                        pdf_path,
                        pdflatex_path=config.pdflatex_path,
                        client=client,
                        enforce_one_page=condense_one_page,
                    )
                    resume_latex = pdf_path.with_suffix(".tex").read_text(
                        encoding="utf-8"
                    )
                    tex_path = pdf_path.with_suffix(".tex")

            # Final fidelity pass on the TAILORED resume: tighten the design to the
            # original's page count (never cutting content) AND run the
            # Discriminator to compare its FORMAT against the original and fix any
            # remaining differences — the same agents the replica flow uses. The
            # content differs (it's tailored), so compare FORMAT ONLY.
            if target_pdf:
                resume_latex, _, _ = _enforce_replica_fidelity(
                    team,
                    config,
                    latex=resume_latex,
                    original_pdf=Path(target_pdf),
                    work_pdf=pdf_path,
                    original_pages=original_pages,
                    discriminator_rounds=discriminator_rounds,
                    compare_format_only=True,
                )
                tex_path.write_text(resume_latex, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - surface message to caller
            compile_error = str(exc)
            tex_path.write_text(resume_latex, encoding="utf-8")
            pdf_path = None  # type: ignore[assignment]
    else:
        tex_path.write_text(resume_latex, encoding="utf-8")
        pdf_path = None  # type: ignore[assignment]

    return PipelineResult(
        template_latex=template_latex,
        resume_latex=resume_latex,
        pdf_path=pdf_path,
        tex_path=tex_path,
        compiled=compiled,
        compile_error=compile_error,
        style_profile=style_profile.text or None,
    )


def _match_style_rounds(
    team: StyleAgentTeam,
    config: Config,
    *,
    resume_latex: str,
    style_profile: StyleProfile,
    original_pdf: Path,
    generated_pdf: Path,
    rounds: int,
) -> str:
    """Run the Style Matcher agent, keeping only versions that still compile.

    Each round compares [ORIGINAL] vs the current [GENERATED] PDF and asks the
    matcher to restyle the LaTeX. A restyled version is accepted only if it
    compiles cleanly; otherwise the previous good version is kept and we stop.
    The accepted PDF (``generated_pdf``) is refreshed each round so the next
    comparison sees the latest output.
    """
    for _ in range(max(0, rounds)):
        restyled = team.match(
            resume_latex=resume_latex,
            style_profile=style_profile,
            original_pdf=original_pdf,
            generated_pdf=generated_pdf,
        )
        if not restyled.strip() or restyled.strip() == resume_latex.strip():
            break
        ok, _ = try_compile(
            restyled, generated_pdf, pdflatex_path=config.pdflatex_path
        )
        if ok:
            resume_latex = restyled
        else:
            # Restyle broke compilation; restore the last good PDF and stop.
            try_compile(
                resume_latex, generated_pdf, pdflatex_path=config.pdflatex_path
            )
            break
    return resume_latex

