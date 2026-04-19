"""Incremental JSON parser for top-level object fields.

Used by the LLM streaming pipeline so the solver can emit SSE events
the moment each top-level key's value is complete, instead of waiting
for the whole AnswerPackage JSON to finish.

Usage::

    parser = TopLevelStreamParser()
    async for chunk in transport.stream():
        for key, value in parser.feed(chunk):
            yield SSEEvent(key, value)
    final = parser.finalize()  # complete JSON text for fallback validation

Design notes
- Pure str-in/str-out; no third-party deps. Handles strings, escapes,
  nested objects and arrays. Numbers/booleans/null are accepted as
  valid top-level values too.
- Detects completion by tracking brace/bracket depth. A top-level
  key/value pair is "complete" when depth returns to 1 (we are inside
  the outer `{}`) and we hit a `,` or the closing `}`.
- For top-level *list* fields whose items each correspond to an SSE
  event (e.g. `solution_steps`), use ``iter_list_items`` to emit one
  event per element as it finishes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class TopLevelStreamParser:
    """Incrementally parse a top-level JSON object.

    The parser buffers raw text and yields ``(key, parsed_value)`` pairs
    as soon as each top-level value's closing token arrives. Order
    matches the wire order, which mirrors the schema field order.
    """

    buf: str = ""
    pos: int = 0  # cursor for unparsed tail
    depth: int = 0
    in_string: bool = False
    escape: bool = False
    started: bool = False  # have we seen the opening `{` yet
    finished: bool = False
    # State machine: looking for either next key or value
    _key_start: int | None = None  # offset where current top-level key begins
    _val_start: int | None = None  # offset where current top-level value begins
    _current_key: str | None = None
    _emitted_keys: list[str] = field(default_factory=list)

    def feed(self, chunk: str) -> Iterator[tuple[str, object]]:
        """Append chunk and yield any newly-completed top-level pairs."""
        if not chunk:
            return
        self.buf += chunk
        yield from self._scan()

    def _scan(self) -> Iterator[tuple[str, object]]:
        i = self.pos
        n = len(self.buf)
        while i < n:
            c = self.buf[i]

            if self.in_string:
                if self.escape:
                    self.escape = False
                elif c == "\\":
                    self.escape = True
                elif c == '"':
                    self.in_string = False
                    # Just closed a string. If we were tracking a key
                    # and depth==1, capture it.
                    if (
                        self.depth == 1
                        and self._key_start is not None
                        and self._val_start is None
                        and self._current_key is None
                    ):
                        try:
                            self._current_key = json.loads(
                                self.buf[self._key_start : i + 1]
                            )
                        except json.JSONDecodeError:
                            self._current_key = None
                        self._key_start = None
                i += 1
                continue

            if c == '"':
                self.in_string = True
                # Beginning of a top-level key (when depth==1 and no
                # value started yet).
                if (
                    self.depth == 1
                    and self._current_key is None
                    and self._val_start is None
                ):
                    self._key_start = i
                # Or beginning of a string value at depth==1.
                elif (
                    self.depth == 1
                    and self._current_key is not None
                    and self._val_start is None
                ):
                    self._val_start = i
                i += 1
                continue

            if c == "{":
                if not self.started:
                    self.started = True
                    self.depth = 1
                    i += 1
                    continue
                # Object value start at depth==1 → begin buffering value
                if (
                    self.depth == 1
                    and self._current_key is not None
                    and self._val_start is None
                ):
                    self._val_start = i
                self.depth += 1
                i += 1
                continue

            if c == "[":
                if (
                    self.depth == 1
                    and self._current_key is not None
                    and self._val_start is None
                ):
                    self._val_start = i
                self.depth += 1
                i += 1
                continue

            if c == "}":
                self.depth -= 1
                i += 1
                if self.depth == 1 and self._val_start is not None:
                    # Object value just closed.
                    pair = self._emit_value(end=i)
                    if pair is not None:
                        yield pair
                    continue
                if self.depth == 0:
                    # Outer object closed. Emit any pending value.
                    if self._val_start is not None and self._current_key is not None:
                        pair = self._emit_value(end=i - 1)
                        if pair is not None:
                            yield pair
                    self.finished = True
                    self.pos = i
                    return
                continue

            if c == "]":
                self.depth -= 1
                i += 1
                if self.depth == 1 and self._val_start is not None:
                    pair = self._emit_value(end=i)
                    if pair is not None:
                        yield pair
                continue

            if c == "," and self.depth == 1:
                # End of a top-level value (number/bool/null without a
                # closing brace), or just comma between keys.
                if self._val_start is not None:
                    pair = self._emit_value(end=i)
                    if pair is not None:
                        yield pair
                i += 1
                continue

            # Begin a primitive value at depth==1
            if (
                self.depth == 1
                and self._current_key is not None
                and self._val_start is None
                and not c.isspace()
                and c != ":"
            ):
                self._val_start = i

            i += 1

        self.pos = i

    def _emit_value(self, *, end: int) -> tuple[str, object] | None:
        if self._current_key is None or self._val_start is None:
            self._current_key = None
            self._val_start = None
            return None
        snippet = self.buf[self._val_start : end].strip().rstrip(",").strip()
        key = self._current_key
        self._current_key = None
        self._val_start = None
        if not snippet:
            return None
        try:
            value = json.loads(snippet)
        except json.JSONDecodeError:
            return None
        self._emitted_keys.append(key)
        return (key, value)

    def finalize(self) -> str:
        """Return the full buffered JSON text (for fallback validation)."""
        return self.buf
