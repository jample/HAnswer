"""Shared local bge-m3 runtime.

Dense + sparse retrieval both use the same FlagEmbedding model. Loading
it once avoids doubling RAM/VRAM when `retrieval.embedder` and
`retrieval.sparse_encoder` are both set to `bge-m3`.
"""

from __future__ import annotations

import threading
from typing import Any

from app.config import settings

_model: Any | None = None
_lock = threading.Lock()


def get_bge_m3_model() -> Any:
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                try:
                    from FlagEmbedding import BGEM3FlagModel
                except ModuleNotFoundError as e:  # pragma: no cover - optional dep
                    raise RuntimeError(
                        "BGE-M3 is not installed. Run "
                        "`pip install -e '.[retrieval]'` before switching "
                        "retrieval.embedder / retrieval.sparse_encoder to 'bge-m3'."
                    ) from e
                except ImportError as e:  # pragma: no cover - optional dep
                    raise RuntimeError(
                        "BGE-M3 dependencies are installed but failed to import. "
                        "The common cause is an incompatible `transformers` "
                        "version. Use the repo's pinned retrieval extra "
                        "(`pip install -e '.[retrieval]'`) which requires "
                        "`transformers<5`."
                    ) from e
                _model = BGEM3FlagModel(
                    settings.retrieval.bge_m3_model,
                    use_fp16=False,
                    device=settings.retrieval.bge_m3_device,
                )
    return _model


def reset_bge_m3_model() -> None:
    global _model
    with _lock:
        _model = None
