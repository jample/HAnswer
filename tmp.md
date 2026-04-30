# 生成可视化 GeoGebra 失败分析与可靠性改造设计

## 1. 本次问题上下文

页面：

- `http://localhost:3333/q/6d827f49-0619-4199-8296-cf0704a97f52`

相关 ID：

- `question_id`: `6d827f49-0619-4199-8296-cf0704a97f52`
- `solution_id`: `eb54b9ff-1ca2-4dc8-ad14-f22b9e397cb6`

前端显示：

```text
可视化运行失败，已回退为规格说明卡片
运行错误: 渲染异常: Missing expected GeoGebra objects: kmin
```

Python console 中关键日志：

```text
GeoGebra Stage 2 initial payload failed for viz viz_chord_midpoint_locus_part1:
GeoGebra property command #1 appears to create an object...
GeoGebra property command #10 targets 'cA', which is not created by commands[]
GeoGebra property command #13 targets 'segmM', which is not created by commands[]
GeoGebra property command #16 targets 'B1', which is not created by commands[]
```

## 2. 日志中的真实链路

日志文件位置：

- `backend/data/logs/llm_prompts.jsonl`
- `backend/data/logs/llm_responses.jsonl`
- `backend/data/logs/visualActions.jsonl`

本次 run 的关键时间线：

```text
2026-04-25T03:55:22Z  solver succeeded
2026-04-25T03:58:19Z  vizspec succeeded
2026-04-25T03:58:19Z  selected/persisted viz_chord_midpoint_locus_part1
2026-04-25T03:58:53Z  geogebra_codegen for viz_chord_midpoint_locus_part1 returned 16 commands
2026-04-25T03:58:53Z  backend static validation rejected primary candidate
2026-04-25T03:58:53Z  answer_job_service tried secondary candidate viz_parameter_range_part2
2026-04-25T03:59:22Z  secondary candidate passed backend static validation and was persisted
2026-04-25T03:59:27Z  frontend GeoGebra runtime partially executed it
2026-04-25T03:59:27Z  frontend failed because expected object kmin was missing
```

Compact log query:

```bash
jq -r 'select(.question_id=="6d827f49-0619-4199-8296-cf0704a97f52" or .solution_id=="eb54b9ff-1ca2-4dc8-ad14-f22b9e397cb6") | [.timestamp,.task,.phase_description,.status,.model,.prompt_tokens,.completion_tokens,.latency_ms] | @tsv' backend/data/logs/llm_responses.jsonl
```

Visual action query:

```bash
jq -r 'select(.question_id=="6d827f49-0619-4199-8296-cf0704a97f52" or .solution_id=="eb54b9ff-1ca2-4dc8-ad14-f22b9e397cb6" or .visualization_id=="viz_parameter_range_part2") | [.timestamp,.source,.phase,.action,.status,(.visualization_id//""),(.component//""),(.error//"")] | @tsv' backend/data/logs/visualActions.jsonl
```

## 3. Prompt 与 response 为什么“找不到”

它们其实已经记录了。

配置在 `backend/config.toml`：

```toml
[storage]
llm_prompt_log_file = "./data/logs/llm_prompts.jsonl"
llm_response_log_file = "./data/logs/llm_responses.jsonl"
```

这两个路径是相对后端工作目录 `backend/` 的，所以真实文件是：

```text
backend/data/logs/llm_prompts.jsonl
backend/data/logs/llm_responses.jsonl
```

本次问题的 4 次 LLM 调用都在日志中：

```text
solver
vizspec
geogebra_codegen / viz_chord_midpoint_locus_part1
geogebra_codegen / viz_parameter_range_part2
```

容易误判“没有日志”的原因：

- JSONL 每条记录是一整行，GeoGebra prompt 很长，一次 `rg` 命中会输出巨大单行。
- `llm_prompts.jsonl` 里 `response_content` 是 `null`，它主要存 messages/schema/metadata。
- 完整 response 在 `llm_responses.jsonl`。
- 推荐用 `jq` 按 `question_id` / `solution_id` / `related.visualization_id` 过滤，不要直接全文 grep。

示例：

```bash
jq 'select(.question_id=="6d827f49-0619-4199-8296-cf0704a97f52")' backend/data/logs/llm_prompts.jsonl
jq 'select(.question_id=="6d827f49-0619-4199-8296-cf0704a97f52")' backend/data/logs/llm_responses.jsonl
```

## 4. 失败原因拆解

### 4.1 Primary candidate 后端失败

Primary spec:

- `viz_chord_midpoint_locus_part1`

失败原因：

- LLM 把对象创建命令放进了 `property_commands`。
- static validator 正确拦截了这类 payload。
- 部分 property command 还引用了未创建对象，例如 `cA`, `segmM`, `B1`。

这不是 runtime 问题，而是 payload 结构层错误。

当前处理：

- 不做额外 LLM retry。
- 进入备用 spec。

这符合“少花 API 成本”的方向。

### 4.2 Secondary candidate 前端失败

Secondary spec:

- `viz_parameter_range_part2`

后端状态：

- `geogebra.codegen.received`: `commands=16`
- `geogebra.sanitize.passed`
- `geogebra.static_validation.passed`
- `visualization.row_persisted`

前端 runtime：

```text
payload.create.step.error:
command: kmin=Abs(Distance(T,K)-2*sqrt(3))
error: assigned object was not created: kmin

payload.create.partial:
#14 "kmin=Abs(Distance(T,K)-2*sqrt(3))" (assigned object was not created: kmin)

payload.expected_objects.check.error:
Missing expected GeoGebra objects: kmin
```

关键点：

- 这个 payload 不是完全坏了。
- 前 13 条左右命令多数已经创建成功。
- `kmin` 是 support numeric measurement，不应该拖垮整个核心图。
- 现在失败是因为 `expected_created_objects` 把 `kmin` 视为必须存在的 hard expected object。

### 4.3 GeoGebra 命令本身的脆弱点

`kmin=Abs(Distance(T,K)-2*sqrt(3))` 在 Apps API 中没有稳定创建对象。

可能原因：

- GeoGebra Apps API 对 `Abs(...)` 解析不稳定，`abs(...)` 更稳。
- 复杂 numeric expression 作为直接 assignment 可能静默失败。
- 应拆成更小对象：

```text
dTK=Distance(T,K)
gap=2*sqrt(3)
kmin=abs(dTK-gap)
kmax=dTK+gap
```

但即便 `kmin` 失败，也不应该让 T/K/locus circles/segments 全部 fallback。

## 5. 现有改造的不足

之前已做的 New Opt 04.25 改造包括：

- lenient `GeoGebraExecutionPayloadDraft`
- 本地 normalization
- prompt few-shot
- command tier: `[core]`, `[support]`, `[annotation]`
- frontend support/annotation failure partial render

但本次暴露出两个不足：

1. **expected object checker 没有根据 command tier 判断 hard/soft。**
   - `kmin` 由 `[support]` command 创建。
   - 但 expected checker 只看 expected object 的 `role/type`。
   - LLM 写了 `role="可能 k 的最小值"`，不含 support/helper/annotation，于是被 hard fail。

2. **后端没有把 expected object 与 command tier 对齐。**
   - 如果 expected object 对应 support command，后端应把它标成 support 或从 hard expected list 中移除。

## 6. 可靠性改造设计

### 6.1 Expected object hard/soft 分类必须来自 command tier

前端构建 `lhs -> tier` map：

```text
t       -> core
T       -> core
K       -> core
segTK   -> core
cT      -> core
cK      -> core
locusT  -> core
locusK  -> core
alpha   -> support
M       -> support
N       -> support
segMN   -> support
kmin    -> support
kmax    -> support
info    -> annotation
```

规则：

- missing core expected object: hard fail / fallback
- missing support expected object: partial pass
- missing annotation/text expected object: trace only

这会直接修复本次 `kmin` 导致整图 fallback 的问题。

### 6.2 后端 sanitizer 对 expected objects 做 tier rewrite

在 `sanitize_geogebra_execution_payload_with_report(...)` 或 `_sanitize_execution_payload(...)` 中：

1. 根据 `commands[]` 左侧 name 建立 `name -> tier`。
2. 遍历 `expected_created_objects`：
   - 如果 name 对应 support command，给 role 加前缀 `support: ...`。
   - 如果 name 对应 annotation command，直接从 expected list 移除。
   - 如果 name 对应 core command，保留为 hard expected。
3. 如果 LLM 自报 expected list 为空，则继续用本地 core inference。

目标：

- 让 frontend 不需要完全信 LLM 的 role 文案。
- 后端持久化后的 payload 已经带正确 runtime 检查语义。

### 6.3 Accidental property creation 本地搬迁

Primary candidate 的失败是：

```text
property command appears to create an object
```

这类错误可以本地修，不需要 LLM retry。

设计：

- 扫描 `property_commands[]`。
- 如果命令有 LHS assignment，例如 `X=...`：
  - 移到 `commands[]` 末尾。
  - 根据 purpose 推断 tier，默认 support。
  - 重新排序 step。
- 如果搬迁后 `commands > 16`：
  - 保留 core commands。
  - support commands 按顺序保留到预算。
  - annotation commands 优先丢弃。
- 剩下 property commands 必须只做 styling/API property。

这能减少“结构错误导致直接换候选/spec-only”的情况，同时不增加 API 调用。

### 6.4 GeoGebra numeric expression 本地 rewrite

对常见 GeoGebra numeric expression 做 deterministic rewrite：

- `Abs(` -> `abs(`
- 对 support numeric object，尽量拆成简单中间量。

针对本次：

```text
kmin=Abs(Distance(T,K)-2*sqrt(3))
kmax=Distance(T,K)+2*sqrt(3)
```

可改写为：

```text
dTK=Distance(T,K)
gap=2*sqrt(3)
kmin=abs(dTK-gap)
kmax=dTK+gap
```

注意：

- `dTK` / `gap` / `kmin` / `kmax` 都应是 support，不是 hard core。
- 如果超预算，宁可丢掉 `kmin/kmax` 对象，也要保留核心图。

### 6.5 Prompt 进一步收紧

Stage 2 prompt 增加：

- Numeric formula displays are support, not core.
- Do not include support numeric objects in hard expected objects.
- If a value is only used for explanation, prefer `Text(...)` with plain formula over a GeoGebra numeric assignment.
- Avoid `Abs(...)`; use `abs(...)`.
- For numeric measurements, prefer simple assignments:

```text
dTK=Distance(T,K)
gap=2*sqrt(3)
kmin=abs(dTK-gap)
kmax=dTK+gap
```

Expected objects rule:

- Only include `[core]` objects in `expected_created_objects`.
- Include `[support]` only when the diagram becomes misleading without it.
- Never include `[annotation]` objects.

### 6.6 Frontend partial display behavior

Current frontend falls back when any hard expected object is missing.

New behavior:

- If all core objects exist:
  - keep iframe visible.
  - show a small status: `部分辅助元素未显示`.
  - log `runtime.partial_passed`.
- If support objects fail:
  - no fallback.
  - record `missing_support`.
- If annotation/style fails:
  - no fallback.
  - record degraded trace.
- If core object fails or object count is zero:
  - fallback card.

## 7. Proposed Implementation Order

1. **Frontend tier-aware expected check**
   - Build `lhs -> tier` from `commands[]`.
   - Use command tier to classify missing expected objects.
   - This should fix the current `kmin` case immediately.

2. **Backend expected object tier rewrite**
   - Persist expected roles aligned with command tier.
   - Prevent future frontend hard-fail from LLM role text.

3. **Backend property creation migration**
   - Move accidental assignment commands from `property_commands` to `commands`.
   - Keep within command budget by dropping annotation/support if needed.

4. **Numeric expression rewrite**
   - `Abs(` -> `abs(`.
   - optionally split known `Distance(...)` numeric support expressions.

5. **Prompt update**
   - Make expected object contract stricter:
     - hard expected = core only.
     - support numeric values should not cause fallback.

## 8. Success Criteria

After the modification:

- The current `viz_parameter_range_part2` should render a core graph even if `kmin` fails.
- Missing `kmin` should produce `runtime.partial_passed`, not fallback.
- Primary candidate errors from object creation inside `property_commands` should often be locally recovered.
- GeoGebra generation should not rely on extra LLM retry by default.
- Logs should clearly distinguish:
  - backend static rejection,
  - frontend core failure,
  - frontend support/annotation partial failure,
  - true empty render.

