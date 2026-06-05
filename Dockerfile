# Resume Automation Bot — production image
# Includes a LaTeX (pdflatex) toolchain so resumes can be compiled to PDF.

FROM python:3.12-slim

# ---- System deps: a practical TeX Live subset + pdflatex ----
# texlive-latex-extra brings titlesec/paracol/enumitem etc; fonts-extra brings
# fontawesome5 and many resume fonts. lmodern improves default font rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        texlive-fonts-extra \
        lmodern \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Python deps (cached separately from app code) ----
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---- App code ----
COPY pyproject.toml ./
COPY src ./src
COPY wsgi.py ./
RUN pip install --no-cache-dir .

# Render (and most hosts) inject $PORT; default to 5000 locally.
ENV PORT=5000 \
    PYTHONUNBUFFERED=1

# Resumes can take ~30–90s to generate; allow a generous worker timeout.
# Use a shell so $PORT is expanded at runtime.
CMD gunicorn wsgi:app \
      --bind 0.0.0.0:${PORT} \
      --workers 2 \
      --threads 4 \
      --timeout 180
