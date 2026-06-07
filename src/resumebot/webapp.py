"""Flask web app: upload a target resume, paste a JD + details, get a tailored PDF."""

from __future__ import annotations

import traceback
import uuid
from pathlib import Path

from flask import (
    Flask,
    abort,
    render_template,
    request,
    send_from_directory,
)
from werkzeug.utils import secure_filename

from .config import OUTPUT_DIR, UPLOAD_DIR, Config
from .generator import AVAILABLE_SECTIONS
from .pipeline import replicate_resume, run_pipeline

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload cap


@app.get("/")
def index():
    return render_template("index.html", sections=AVAILABLE_SECTIONS)


@app.post("/generate")
def generate():
    api_key = (request.form.get("api_key") or "").strip()
    try:
        config = Config.load(api_key=api_key)
    except RuntimeError as exc:
        return render_template("index.html", sections=AVAILABLE_SECTIONS, error=str(exc)), 400

    job_description = (request.form.get("job_description") or "").strip()
    details = (request.form.get("details") or "").strip()
    instructions = (request.form.get("instructions") or "").strip()
    match_style = bool(request.form.get("match_style"))
    upload = request.files.get("target_pdf")

    # Which sections to tailor. If the user ticks none, tailor everything.
    selected_sections = [s for s in AVAILABLE_SECTIONS if request.form.get(f"section::{s}")]
    sections = selected_sections or None

    if not upload or not upload.filename:
        return render_template(
            "index.html", sections=AVAILABLE_SECTIONS, error="Please upload a target resume PDF."
        ), 400
    if not job_description:
        return render_template(
            "index.html", sections=AVAILABLE_SECTIONS, error="Please paste the job description."
        ), 400
    # Details are optional: when a section is selected but no details are given,
    # the bot keeps the template's existing content for that section.

    # Save the uploaded target PDF.
    safe_name = secure_filename(upload.filename) or "target.pdf"
    target_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    upload.save(target_path)

    basename = f"resume_{uuid.uuid4().hex[:8]}"

    try:
        result = run_pipeline(
            config,
            target_pdf=target_path,
            template_latex=None,
            job_description=job_description,
            details=details,
            sections=sections,
            instructions=instructions,
            output_basename=basename,
            compile_pdf=True,
            match_style=match_style,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return render_template(
            "index.html", sections=AVAILABLE_SECTIONS, error=f"Generation failed: {exc}"
        ), 500

    return render_template(
        "result.html",
        basename=basename,
        compiled=result.compiled,
        compile_error=result.compile_error,
        latex=result.resume_latex,
    )


@app.get("/download/<path:filename>")
def download(filename: str):
    # Only allow files inside OUTPUT_DIR.
    safe = secure_filename(filename)
    target = OUTPUT_DIR / safe
    if not target.exists():
        abort(404)
    return send_from_directory(OUTPUT_DIR, safe, as_attachment=True)


@app.get("/view/<path:filename>")
def view(filename: str):
    """Serve a generated file INLINE (for embedding in a preview/compare frame)."""
    safe = secure_filename(filename)
    if not (OUTPUT_DIR / safe).exists():
        abort(404)
    return send_from_directory(OUTPUT_DIR, safe, as_attachment=False)


@app.get("/view-upload/<path:filename>")
def view_upload(filename: str):
    """Serve an uploaded original PDF INLINE (for the side-by-side comparison)."""
    safe = secure_filename(filename)
    if not (UPLOAD_DIR / safe).exists():
        abort(404)
    return send_from_directory(UPLOAD_DIR, safe, as_attachment=False)


@app.get("/replicate")
def replicate_page():
    return render_template("replicate.html")


@app.post("/replicate")
def replicate():
    api_key = (request.form.get("api_key") or "").strip()
    try:
        config = Config.load(api_key=api_key)
    except RuntimeError as exc:
        return render_template("replicate.html", error=str(exc)), 400

    upload = request.files.get("target_pdf")
    if not upload or not upload.filename:
        return render_template(
            "replicate.html", error="Please upload a resume PDF to replicate."
        ), 400

    # Save the uploaded original so it can be shown next to the replica.
    safe_name = secure_filename(upload.filename) or "target.pdf"
    upload_name = f"{uuid.uuid4().hex}_{safe_name}"
    target_path = UPLOAD_DIR / upload_name
    upload.save(target_path)

    basename = f"replica_{uuid.uuid4().hex[:8]}"

    try:
        result = replicate_resume(
            config,
            target_path,
            output_basename=basename,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return render_template(
            "replicate.html", error=f"Replication failed: {exc}"
        ), 500

    return render_template(
        "replicate_result.html",
        basename=basename,
        original_filename=upload_name,
        compiled=result.compiled,
        compile_error=result.compile_error,
        latex=result.template_latex,
        style_profile=result.style_profile,
        discriminator_feedback=result.discriminator_feedback,
        replica_pages=result.replica_pages,
    )


def main() -> None:
    # Keys are supplied per-request via the web form, so the server itself does
    # not need an API key to start. Bind to 0.0.0.0 so it works in containers.
    import os

    OUTPUT_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    try:
        port = int(os.getenv("PORT", "5000"))
    except ValueError:
        port = 5000
    host = os.getenv("HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
