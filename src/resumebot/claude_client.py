"""Thin wrapper around the Anthropic (Claude) API.

Handles sending text prompts and PDF documents to Claude and extracting the
text response. Claude can read PDFs natively, so we pass the resume PDF as a
document block to preserve layout/formatting understanding.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

import anthropic

from .config import Config

# Generous ceiling so full LaTeX documents are never truncated.
MAX_TOKENS = 8000

# A PDF to attach: either just a path, or a (label, path) pair. The label is
# shown to Claude before the document so it can tell multiple PDFs apart.
PdfArg = Union[Path, str, Tuple[str, Union[Path, str]]]


class ClaudeClient:
    def __init__(self, config: Config):
        self._config = config
        self._client = anthropic.Anthropic(api_key=config.api_key)

    def ask(
        self,
        prompt: str,
        *,
        pdf_path: Optional[Path] = None,
        pdfs: Optional[Sequence[PdfArg]] = None,
        system: Optional[str] = None,
        max_tokens: int = MAX_TOKENS,
    ) -> str:
        """Send a prompt (optionally with one or more PDFs) and return the reply.

        Use ``pdf_path`` for a single document, or ``pdfs`` for several. Items in
        ``pdfs`` may be a path or a ``(label, path)`` tuple; the label is sent as
        a small text marker before the document so Claude can distinguish them
        (e.g. "ORIGINAL" vs "GENERATED").
        """
        content: list[dict] = []

        attachments: list[PdfArg] = []
        if pdf_path is not None:
            attachments.append(pdf_path)
        if pdfs:
            attachments.extend(pdfs)

        for item in attachments:
            if isinstance(item, tuple):
                label, path = item
                content.append({"type": "text", "text": f"[{label}]"})
            else:
                path = item
            pdf_bytes = Path(path).read_bytes()
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                    },
                }
            )

        content.append({"type": "text", "text": prompt})

        kwargs: dict = {
            "model": self._config.model,  # e.g. claude-sonnet-4-6
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = system

        message = self._client.messages.create(**kwargs)

        return "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()
