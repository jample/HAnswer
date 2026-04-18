"""End-to-end smoke test: image → ParsedQuestion via real Gemini.

Does NOT persist to the DB. Use this to sanity-check the ParserPrompt
wording and the GoogleGeminiTransport wiring against a real sample.

Usage:
    cd backend
    python -m scripts.smoke_parse ../data/samples/q1.jpg --subject math
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import sys
from pathlib import Path

from app.config import settings
from app.prompts import PromptRegistry
from app.schemas import ParsedQuestion
from app.services.gemini_transport import GoogleGeminiTransport
from app.services.llm_client import GeminiClient


def _guess_mime(path: Path) -> str:
    mt, _ = mimetypes.guess_type(path.name)
    if mt in {"image/jpeg", "image/png", "image/webp", "image/heic"}:
        return mt
    # Fallback for .heic on some systems.
    if path.suffix.lower() in {".heic", ".heif"}:
        return "image/heic"
    raise SystemExit(f"Unsupported image type: {path}")


async def _main_async(image_path: Path, subject_hint: str | None) -> int:
    if not image_path.exists():
        print(f"ERROR: {image_path} not found.", file=sys.stderr)
        print(
            "Drop the sample JPG at that path, then re-run. "
            "See data/samples/README.md.",
            file=sys.stderr,
        )
        return 2
    if not settings.gemini.api_key:
        print("ERROR: gemini.api_key is empty in config.toml.", file=sys.stderr)
        return 2

    mime = _guess_mime(image_path)
    image_bytes = image_path.read_bytes()

    parser = PromptRegistry.get("parser")
    kwargs = {"subject_hint": subject_hint} if subject_hint else {}

    # Preview first — useful when iterating on prompt wording.
    print(parser.preview(**kwargs))
    print("\n--- calling Gemini …  ---\n")

    messages = parser.build_multimodal(image_bytes, mime, **kwargs)

    client = GeminiClient(GoogleGeminiTransport())
    result: ParsedQuestion = await client.call_structured(
        template=parser,
        model=settings.gemini.model_parser,
        model_cls=ParsedQuestion,
        template_kwargs=kwargs,
        messages_override=messages,
    )
    print(result.model_dump_json(indent=2, by_alias=False))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--subject", choices=["math", "physics"], default=None)
    args = ap.parse_args()
    sys.exit(asyncio.run(_main_async(args.image, args.subject)))


if __name__ == "__main__":
    main()
