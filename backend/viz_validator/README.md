# Viz AST validator (§3.3.3)

Node helper that parses LLM-emitted JSXGraph code with `acorn` and
rejects anything outside the HAnswer allow-list.

```bash
cd backend/viz_validator
npm install
```

The Python wrapper at `app/services/viz_validator.py` spawns this via
`node validate.mjs` on every viz before it is persisted.
