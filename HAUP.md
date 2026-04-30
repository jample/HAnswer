# HAUP

## Scope

This document records the parser-side optimization for `上传并解析` only.

Affected flows:

- upload image -> parse question
- rescan stored image -> parse question
- replace image -> parse question

Explicitly out of scope:

- `生成解答`
- solver streaming behavior
- visualization generation

## Problem

The previous parser path used streamed structured JSON generation for image parsing.
That design had two weaknesses for `上传并解析`:

1. The endpoint does not expose partial parser output to the frontend, so streaming gave no user-facing latency benefit.
2. A stalled stream triggered a second non-stream recovery call, which could stretch one parser request into multiple minutes.

The backend log pattern looked like:

- parser starts with stream mode
- stream stalls around `parser_timeout_s`
- fallback bulk call runs with the larger recovery timeout

This made upload parsing unstable and slow.

## New Design

### 1. Parser calls are non-streaming

`上传并解析` now always uses a single non-stream structured Gemini call.

Rationale:

- the endpoint only needs one final `ParsedQuestion`
- no SSE or partial parser UX exists here
- removing streaming removes one full failure mode

### 2. Parser images are preprocessed before Gemini

The stored source image remains unchanged, but the bytes sent to Gemini are now optimized for parsing:

- EXIF orientation is normalized
- oversized images are downscaled
- non-JPEG or very large images are re-encoded to JPEG for the parser path
- if preprocessing would not help, the original bytes are kept

This reduces request payload size and makes parser latency more predictable.

### 3. Parser prompt is smaller

The parser prompt no longer embeds the full JSON schema text in the system prompt.
The transport still sends the exact `response_json_schema` contract to Gemini, so schema enforcement remains intact while prompt size is reduced.

### 4. Parser timing is bounded more tightly

Default parser settings are now oriented toward fast completion:

- `stream_parser_json = false`
- `parser_timeout_s = 90`

The parser path no longer begins with a streamed 120s attempt followed by a long 300s bulk recovery.

### 5. Parser-specific operational logs were added

The ingest service now logs:

- original mime and byte size
- parser mime and byte size
- whether preprocessing was applied
- preprocessing reason
- parser elapsed time
- parsed subject / grade band / difficulty / confidence

This makes slow parser calls diagnosable without affecting solver or visualization logs.

## Realized Changes

### Backend

[backend/app/services/ingest_service.py](/Users/jianbo/code/cccode/HAnswer/backend/app/services/ingest_service.py)

- added parser image preprocessing
- unified parser call path for upload / rescan / replace-image
- forced parser calls to `stream=False`
- added parser latency and preprocessing logs

[backend/app/prompts/parser_prompt.py](/Users/jianbo/code/cccode/HAnswer/backend/app/prompts/parser_prompt.py)

- removed full schema dump from the system prompt
- kept the semantic parsing rules and required field list

[backend/app/config.py](/Users/jianbo/code/cccode/HAnswer/backend/app/config.py)
[backend/config.example.toml](/Users/jianbo/code/cccode/HAnswer/backend/config.example.toml)

- changed parser defaults to non-stream
- lowered default parser timeout to 90s

## Stability Intent

The parser path is now optimized for:

- one call instead of stream-plus-recovery
- smaller parser payloads
- lower tail latency
- clearer failure diagnosis

The solver path is intentionally unchanged, because that is the place where structured streaming still makes product sense.
