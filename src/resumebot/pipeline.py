"""High-level pipeline tying the steps together (used by CLI and web app)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .claude_client import ClaudeClient
from .compiler import compile_latex, try_compile
from .config import Config
from .generator import generate_resume
from .latex_convert import (
    pdf_to_latex_template,
    refine_template_against_original,
)


@dataclass
class PipelineResult:
    template_latex: str
    resume_latex: str
    pdf_path: Optional[Path]
    tex_path: Path
    compiled: bool
    compile_error: Optional[str] = None


def build_template(config: Config, target_pdf: Path, *, refine_rounds: int = 2) -> str:
    """Step 1 only: turn a target resume PDF into a LaTeX template.

    Runs the visual-refinement loop so the template matches the original PDF.
    """
    client = ClaudeClient(config)
    return _build_refined_template(
        client, config, Path(target_pdf), refine_rounds=refine_rounds
    )


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
) -> PipelineResult:
    """Run the full flow.

    Either ``target_pdf`` (to derive a template) or a pre-built ``template_latex``
    must be supplied. ``sections`` optionally limits which resume sections are
    tailored to the JD (the rest stay as the original). ``refine_rounds``
    controls how many visual-comparison passes are used to make the template
    match the original PDF. Returns a :class:`PipelineResult`.
    """
    if not template_latex and not target_pdf:
        raise ValueError("Provide either target_pdf or template_latex.")

    client = ClaudeClient(config)

    if not template_latex:
        template_latex = _build_refined_template(
            client, config, Path(target_pdf), refine_rounds=refine_rounds
        )

    resume_latex = generate_resume(
        client,
        template_latex=template_latex,
        job_description=job_description,
        details=details,
        sections=sections,
        instructions=instructions,
    )

    from .config import OUTPUT_DIR

    pdf_path = OUTPUT_DIR / f"{output_basename}.pdf"
    tex_path = OUTPUT_DIR / f"{output_basename}.tex"

    compiled = False
    compile_error: Optional[str] = None

    if compile_pdf:
        try:
            compile_latex(
                resume_latex,
                pdf_path,
                pdflatex_path=config.pdflatex_path,
                client=client,
                # Never silently trim/remove content to fit one page — the user
                # wants all content preserved. Compilation still fixes errors.
                enforce_one_page=False,
            )
            compiled = True
            # compile_latex rewrites the .tex (possibly repaired); reload it.
            resume_latex = pdf_path.with_suffix(".tex").read_text(encoding="utf-8")
            tex_path = pdf_path.with_suffix(".tex")
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
    )

