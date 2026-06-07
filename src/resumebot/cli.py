"""Command-line interface for the resume automation bot.

Examples
--------
# Make a template from a target resume and save it for reuse:
python -m resumebot template --target path/to/target_resume.pdf -o my_template.tex

# Generate a tailored resume in one shot from a target PDF + a JD file + details:
python -m resumebot generate \
    --target path/to/target_resume.pdf \
    --jd path/to/job_description.txt \
    --details path/to/my_details.txt \
    -o tailored_resume

# Reuse a saved template instead of re-analysing the PDF every time:
python -m resumebot generate \
    --template my_template.tex \
    --jd job.txt --details me.txt -o tailored_resume
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .generator import AVAILABLE_SECTIONS
from .pipeline import build_template, run_pipeline


def _read(path: str | None) -> str | None:
    if path is None:
        return None
    return Path(path).read_text(encoding="utf-8")


def _resolve_sections(raw: str | None):
    """Parse the --sections value.

    Returns None to tailor everything, a list of canonical section names, or
    False if an unknown section was given (an error message is printed).
    """
    if not raw or raw.strip().lower() == "all":
        return None

    requested = [part.strip() for part in raw.split(",") if part.strip()]
    lookup = {s.lower(): s for s in AVAILABLE_SECTIONS}
    resolved: list[str] = []
    for item in requested:
        canonical = lookup.get(item.lower())
        if canonical is None:
            print(
                f"error: unknown section '{item}'. Choose from: "
                + ", ".join(AVAILABLE_SECTIONS),
                file=sys.stderr,
            )
            return False
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved or None


def _cmd_template(args: argparse.Namespace) -> int:
    config = Config.load()
    print(f"Analysing target resume: {args.target} ...")
    template = build_template(config, Path(args.target))
    out = Path(args.output)
    out.write_text(template, encoding="utf-8")
    print(f"LaTeX template written to {out}")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    config = Config.load()

    if not args.target and not args.template:
        print("error: provide --target <pdf> or --template <tex>", file=sys.stderr)
        return 2

    job_description = _read(args.jd) or args.jd_text or ""
    details = _read(args.details) or args.details_text or ""
    if not job_description.strip():
        print("error: a job description is required (--jd or --jd-text)", file=sys.stderr)
        return 2

    sections = _resolve_sections(args.sections)
    if sections is False:
        return 2
    if not details.strip() and sections is None:
        print("error: candidate details are required (--details or --details-text)", file=sys.stderr)
        return 2

    template_latex = _read(args.template)

    print("Generating tailored resume with Claude ...")
    result = run_pipeline(
        config,
        target_pdf=Path(args.target) if args.target else None,
        template_latex=template_latex,
        job_description=job_description,
        details=details,
        sections=sections,
        instructions=(_read(args.instructions) or args.instructions_text or ""),
        output_basename=args.output,
        compile_pdf=not args.no_compile,
        match_style=not args.no_style_match,
        enforce_one_page=not args.no_one_page,
    )

    print(f"LaTeX source: {result.tex_path}")
    if result.compiled and result.pdf_path:
        print(f"Compiled PDF: {result.pdf_path}")
    elif args.no_compile:
        print("Skipped PDF compilation (--no-compile).")
    else:
        print("PDF compilation failed; the .tex was still saved.")
        if result.compile_error:
            print(result.compile_error, file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resumebot",
        description="Convert a target resume to LaTeX and tailor new resumes to a JD.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_tpl = sub.add_parser("template", help="Build a LaTeX template from a target resume PDF.")
    p_tpl.add_argument("--target", required=True, help="Path to the target resume PDF.")
    p_tpl.add_argument("-o", "--output", default="template.tex", help="Where to save the .tex template.")
    p_tpl.set_defaults(func=_cmd_template)

    p_gen = sub.add_parser("generate", help="Generate a tailored resume.")
    src = p_gen.add_argument_group("template source (choose one)")
    src.add_argument("--target", help="Target resume PDF to derive the style from.")
    src.add_argument("--template", help="Pre-built .tex template to reuse.")
    p_gen.add_argument("--jd", help="Path to a job-description text file.")
    p_gen.add_argument("--jd-text", help="Job description provided inline.")
    p_gen.add_argument("--details", help="Path to a file with the candidate's details.")
    p_gen.add_argument("--details-text", help="Candidate details provided inline.")
    p_gen.add_argument(
        "--sections",
        help=(
            "Comma-separated sections to tailor to the JD (others stay as the "
            "original). Choose from: " + ", ".join(AVAILABLE_SECTIONS) + ". "
            "Use 'all' or omit to tailor everything. "
            'Example: --sections "Profile Summary, Work Experience"'
        ),
    )
    p_gen.add_argument(
        "--instructions",
        help="Path to a file with free-text writing-style instructions (how to write the content).",
    )
    p_gen.add_argument(
        "--instructions-text",
        help="Writing-style instructions provided inline (how to write the content).",
    )
    p_gen.add_argument("-o", "--output", default="resume", help="Output base name (no extension).")
    p_gen.add_argument("--no-compile", action="store_true", help="Skip PDF compilation.")
    p_gen.add_argument(
        "--no-style-match",
        action="store_true",
        help=(
            "Disable the two-agent style copying (Style Analyst + Style Matcher) "
            "that makes the result mirror the uploaded resume's style."
        ),
    )
    p_gen.add_argument(
        "--no-one-page",
        action="store_true",
        help="Do not condense the resume to fit on a single page.",
    )
    p_gen.set_defaults(func=_cmd_generate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
