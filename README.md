# Resume Automation Bot

Turn any resume PDF into a reusable **LaTeX template**, then generate a brand-new
resume tailored to any **job description** — in the exact same style. Claude does
all the analysis and heavy lifting; the bot compiles the result to a polished PDF.

```
Target resume PDF  ─┐
Job description    ─┼──►  Claude  ──►  Tailored LaTeX  ──►  pdflatex  ──►  resume.pdf
Your details       ─┘
```

## What it does

1. **Reads your target resume PDF** (the design you want to copy) and rebuilds it
   as clean, reusable LaTeX.
2. **Refills that template** with your real details, **tailored to the job
   description** (relevant experience first, JD keywords surfaced, impact-focused
   bullets — without inventing anything).
3. **Compiles to PDF** and, if LaTeX errors occur, asks Claude to auto-repair them.

You get both the **compiled PDF** and the **LaTeX source**.

---

## 1. Get a Claude (Anthropic) API key

You said you don't have a key yet — here's how:

1. Go to **https://console.anthropic.com/** and sign up / log in.
2. Open **Settings → API Keys** (direct link:
   https://console.anthropic.com/settings/keys).
3. Click **Create Key**, give it a name, and copy the value
   (it starts with `sk-ant-...`). You only see it once.
4. Add **billing**: Settings → Billing → add a payment method and a little credit
   (a few dollars is plenty — each resume costs roughly a cent or two).

Then create your `.env`:

```bash
cp .env.example .env
```

Open `.env` and paste your key into `ANTHROPIC_API_KEY=`.

---

## 2. Install

You need **Python 3.10+** and a **LaTeX** install that provides `pdflatex`
(TeX Live on macOS/Linux, MiKTeX on Windows). On macOS:

```bash
brew install --cask mactex-no-gui     # provides pdflatex
```

Then set up the Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .
```

> No LaTeX installed? You can still use the bot with `--no-compile` (CLI) and
> compile the `.tex` on https://overleaf.com.

---

## 3a. Use it — Web app

```bash
python run_web.py
```

Open **http://127.0.0.1:5000**, upload your target resume PDF, paste the job
description and your details, and click **Generate**. Download the PDF and `.tex`.

## 3b. Use it — Command line

**One-shot** (analyse target PDF + generate tailored resume):

```bash
resumebot generate \
  --target target_resume.pdf \
  --jd job_description.txt \
  --details my_details.txt \
  -o tailored_resume
```

Outputs land in `output/` as `tailored_resume.pdf` and `tailored_resume.tex`.

**Reuse a template** (faster — skip re-analysing the PDF each time):

```bash
# Build the template once:
resumebot template --target target_resume.pdf -o my_template.tex

# Then generate as many tailored resumes as you like:
resumebot generate --template my_template.tex \
  --jd another_job.txt --details my_details.txt -o resume_for_job2
```

Inline text instead of files:

```bash
resumebot generate --template my_template.tex \
  --jd-text "Senior Python role…" \
  --details-text "Jane Doe, 5y Python, AWS…" -o resume3
```

Skip PDF compilation (just produce `.tex`):

```bash
resumebot generate --template my_template.tex --jd job.txt --details me.txt --no-compile -o r
```

---

## Deploy online (Render, with LaTeX)

The app needs `pdflatex`, so it ships with a `Dockerfile` that bundles a TeX
Live toolchain. In this deployment each user enters their own Anthropic API key
in the web UI (nothing is stored server-side).

**One-time, via the Render dashboard:**

1. Push this project to a GitHub repo.
2. Go to https://dashboard.render.com → **New +** → **Blueprint**.
3. Pick your repo. Render reads [`render.yaml`](render.yaml) and creates a Docker
   web service automatically. Click **Apply**.
4. Wait for the first build (installing TeX Live takes a few minutes). When it's
   live, open the `*.onrender.com` URL.

No environment variables are required because keys are entered per-user. To run
in **single shared-key** mode instead, uncomment `ANTHROPIC_API_KEY` in
[`render.yaml`](render.yaml) and set it as a secret in the Render dashboard.

**Other hosts:** any platform that builds a `Dockerfile` works the same way
(Railway, Fly.io, Google Cloud Run). The container serves
`gunicorn wsgi:app` on `$PORT`.

**Test the container locally (if you have Docker):**

```bash
docker build -t resumebot .
docker run -p 5000:5000 resumebot
# open http://localhost:5000
```

---

## Project layout

```
src/resumebot/
  config.py          # loads .env settings
  claude_client.py   # Anthropic API wrapper (sends PDFs + prompts)
  latex_convert.py   # step 1: target PDF -> LaTeX template
  generator.py       # step 2: template + JD + details -> tailored LaTeX
  compiler.py        # step 3: LaTeX -> PDF (with Claude auto-repair)
  pipeline.py        # ties the steps together
  cli.py             # command-line interface
  webapp.py          # Flask web app
  templates/         # web UI (index.html, result.html)
run_web.py           # convenience web launcher
```

## Configuration (`.env`)

| Variable            | Purpose                                            | Default                      |
|---------------------|----------------------------------------------------|------------------------------|
| `ANTHROPIC_API_KEY` | Your Claude API key (required)                     | —                            |
| `CLAUDE_MODEL`      | Model to use                                       | `claude-sonnet-4-6`          |
| `PDFLATEX_PATH`     | Path to `pdflatex`                                 | `pdflatex`                   |
| `PORT`              | Web server port                                    | `5000`                       |

## Notes

- The bot is instructed **not to fabricate** experience — it only tailors what you
  provide. Always proofread the output.
- Generated files go to `output/`; uploads to `uploads/`. Both are git-ignored.
