# Sample questions

Place reference question images here. The smoke script below assumes:

- `q1.jpg` — 2026·北京西城·三帆零模 第28题 (新定义 · 平移 · 最值)
  - 学科: math
  - 学段: senior (高中)
  - 话题: 新定义 / 圆 / 最值 / 平移变换

Run the smoke test from the repo root:

```bash
cd backend
python -m scripts.smoke_parse ../data/samples/q1.jpg --subject math
```

This exercises: image load → ParserPrompt.build_multimodal →
GoogleGeminiTransport → JSON validation (repair loop if needed) →
ParsedQuestion pydantic model. It does NOT write to the DB.
