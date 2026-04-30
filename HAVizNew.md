# HAVizNew: 当前代码库中“生成可视化”的设计与实现说明

本文不是新的方案草案，而是基于当前代码实现整理出来的现状说明。目标是把“生成可视化”的真实执行路径、数据落点、校验链路，以及日志捕获机制说清楚，便于后续继续做优化。

---

## 1. 当前实现结论

当前代码库里，“生成可视化”已经不是最早那种单一 `VizCoder -> JSXGraph` 的路径，而是分成了两套：

1. **当前主路径（实际在 `/api/answer/{question_id}` 和后台 answer job 中启用）**
   - Stage 1：`vizspec` 生成 `VisualizationSpecBundle`
   - 选择一个 `recommended` 且更稳定的 spec
   - Stage 2：`geogebra_codegen` 基于选中的 spec 生成 GeoGebra 指令
   - 本地 sanitize + headless runtime validate
   - 持久化为 `VisualizationRow`
   - 前端通过 `GeoGebraSandbox` 渲染

2. **保留的旧/回退路径（仍然存在于代码里，但不是主入口当前默认路径）**
   - `vizcoder_service.py`
   - 先尝试 batch `vizcoder`
   - 如果 batch 没产出有效图，再走 `vizplanner + vizitem`
   - 同时支持 `jsxgraph` 和 `geogebra`

所以从“当前真实运行路径”看，系统已经偏向：

- **Stage 1 做教学可视化规格规划**
- **Stage 2 默认生成 GeoGebra**
- **JSXGraph 仍保留，但主要在旧路径/兼容路径中**

---

## 2. 当前主路径的实际执行流程

### 2.1 入口

主 SSE 接口在：

- `backend/app/routers/answer.py`

当前 `/api/answer/{question_id}` 的执行顺序是：

1. 先调用 solver 生成完整 `AnswerPackage`
2. 然后进入 visualizing 阶段
3. `generate_visualization_spec_bundle(...)`
4. `select_recommended_visualization(...)`
5. `persist_visualization_spec_bundle(...)`
6. `generate_geogebra_visualization_or_fallback(...)`
7. `_build_visualization_row(...)`
8. 写入 `visualizations` 表
9. 更新 `solution.visualizations_json`
10. 向前端流出 `visualization` 事件

后台异步 job 路径在：

- `backend/app/services/answer_job_service.py`

这里的 visualizing stage 与上面 SSE 路径基本一致，只是额外带了：

- stage review
- 人工确认状态
- 失败后的更友好状态维护

### 2.2 Stage 1：生成 VisualizationSpec

实现文件：

- `backend/app/services/visualization_spec_service.py`
- schema：`backend/app/schemas/visualization_spec.py`
- prompt：`backend/app/prompts/vizspec_prompt.py`

当前 Stage 1 的输入不是原始完整 `AnswerPackage`，而是先经过：

- `summarize_answer_for_visualization(answer_package)`

做裁剪，只保留真正影响可视化设计的部分，例如：

- `question_understanding`
- `key_points_of_question`
- `solution_steps` 的精简版本
- `key_points_of_answer`
- `method_pattern` 的精简版本

明确被裁掉的包括：

- `similar_questions`
- `knowledge_points`
- `self_check`
- 每步较重的解释性字段

这说明当前代码已经把一个重要优化点落地了：

- **Stage 1 prompt 已经在主动缩小输入上下文，避免 vizspec 调用过大、过慢**

Stage 1 输出类型是：

- `VisualizationSpecBundle`

其中要求：

- `visualizations` 数量 1~2 个
- 至少有一个 `recommended=true`，除非所有项都是 `needs_revision`

随后通过：

- `select_recommended_visualization(bundle)`

选择当前最适合继续进入 Stage 2 的 spec。

选择规则不是简单“第一个 recommended”，而是：

1. 优先 `recommended=true`
2. 优先 `overall_readiness in {"ready", "mostly_ready"}`
3. 如果没有，就退化为 bundle 中“最稳定”的那个
4. 最终按 `priority` 和 `implementation_stability_score` 排序

### 2.3 Stage 1 结果持久化

Stage 1 结果不会直接变成最终可视化，而是先写入 solution 的规划字段：

- `question_solutions.visualization_plan_json`

持久化逻辑在：

- `persist_visualization_spec_bundle(...)`

写入内容包括：

- 完整 `VisualizationSpecBundle`
- `selected_visualization_id`
- `selected_visualization`

这意味着当前系统已经把：

- “规划结果”
- “最终渲染结果”

拆成了两个独立资产，后续做 review、rerun、对比优化都更容易。

### 2.4 Stage 2：根据选中的 spec 生成 GeoGebra

实现文件：

- `backend/app/services/geogebra_codegen_service.py`
- prompt：`backend/app/prompts/geogebra_codegen_prompt.py`

当前主路径不会直接让 LLM 输出任意前端代码，而是让它输出：

- `GeoGebraExecutionPayload`
- `commands`
- `property_commands`
- `interaction_objects`
- `optional_script`
- `expected_created_objects`

然后在服务层强制做 Stage 2 合同校验：

- `commands` 至少有一条
- `commands` / `property_commands` 必须按 step 排序
- `interaction_objects` / `expected_created_objects` 必须引用已创建对象
- 默认主路径只接受 `command_only`，不使用脚本

如果不满足，就直接视为 Stage 2 失败。

---

## 3. Stage 2 的校验与降级机制

### 3.1 GeoGebra sanitize

实现文件：

- `backend/app/services/geogebra_validator.py`

Stage 2 生成出来的 `VisualizationDraft` 不会直接入库，而是先经过：

- `sanitize_geogebra_visualization_with_report(...)`

这个步骤做的是**本地、确定性修正**，不是再调 LLM。

当前主要处理：

1. **标识符改写**
   - 避免 Greek aliases，如 `alpha`, `beta`, `pi`
   - 避免 GeoGebra 保留名，如 `xAxis`, `yAxis`, `e`, `i`

2. **参数绑定校验**
   - `params[].name` 必须对应某个已定义对象

3. **动画驱动绑定校验**
   - `animation.drives[]` 必须对应某个已定义对象

sanitize 完成后才会升级成严格的：

- `Visualization`

### 3.2 GeoGebra 严格 schema / anti-pattern 校验

严格模型在：

- `backend/app/schemas/llm.py` 中的 `Visualization`

这里有大量 GeoGebra anti-pattern guard，用来挡住历史上会导致 Apps API 静默失败的命令形态，例如：

- 过长命令 / 命令过多
- 不合规 view directives
- 某些 `Vector(...)` / `Translate(...)` 反模式
- `SetColor` 使用命名颜色
- 一些 GeoGebra 不稳定写法

这一步的定位是：

- **把“看起来像合法字符串、但运行会坏”的命令尽早拦在后端**

### 3.3 Static validator

GeoGebra 通过 schema 之后，当前主路径只跑后端静态 validator：

- `validate_geogebra_execution_payload(...)`

它不再调用后端浏览器或 Node GeoGebra sandbox，而是检查：

- 命令数量预算
- 命令长度与换行
- object definition/reference graph
- property command target 是否已创建
- 已知 GeoGebra Apps API 脆弱写法，例如 point-plus-vector、`Vector(B-A)`、条件对象创建、命名颜色、`SetValue`
- 默认主路径不允许 optional script

所以当前主路径不是“LLM 输出后直接相信”，而是：

1. LLM 生成
2. 本地 sanitize
3. 严格 schema 校验
4. static validator 校验
5. 才允许持久化

真实渲染由前端 `GeoGebraSandbox` 执行并上报 trace；后端不再在用户等待链路中启动浏览器 validator。

### 3.4 失败时如何降级

主路径当前不是“Stage 2 失败就整题无可视化”，而是允许降级为 **spec-only fallback**。

逻辑在：

- `generate_geogebra_visualization_or_fallback(...)`
- `answer_job_service._build_visualization_row(...)`

如果 Stage 2 GeoGebra 失败：

- 仍然会保留 Stage 1 选中的 `VisualizationSpec`
- 生成一个 `VisualizationRow`
- `engine` 仍记为 `geogebra`
- 但 `ggb_commands_json` 为空
- `spec_json` 保留完整 spec
- `params_json` / `animation_json` 从 spec 推导

前端此时不会渲染真实 GeoGebra 图，而是显示规格说明 fallback 卡片。

这点很关键：当前系统已经从“全有或全无”变成了“至少保留教学意图与规格资产”。

---

## 4. 旧路径 / 回退路径：vizcoder_service

实现文件：

- `backend/app/services/vizcoder_service.py`

这套逻辑仍然完整存在，主要分两层：

### 4.1 Batch 路径

- `generate_visualizations_batch(...)`

直接调：

- `vizcoder` prompt

让模型一次性返回 1~2 个 `VisualizationDraft`。

然后逐个执行：

- `_prepare_visualization_for_persist(...)`

其中：

- `jsxgraph` 走 `validate_jsx_code(...)`
- `geogebra` 走 `validate_geogebra_visualization(...)`

### 4.2 Storyboard 回退路径

如果 batch 没产出任何可用结果，则：

1. `plan_visualization_storyboard(...)`
2. `generate_visualizations_from_storyboard(...)`
3. 逐个 `vizitem` 生成
4. 每个 item 单独 validate

这套路径仍然有价值，因为它保留了：

- 更细粒度逐图生成
- 失败隔离到单个 visualization
- 同时支持 `jsxgraph` 和 `geogebra`

但要注意：

- **它不是当前 `/api/answer/{question_id}` 主流程正在走的路径**
- 当前主流程已经切到 `vizspec -> geogebra_codegen`

所以后续做优化时，要先明确：

- 是优化当前主路径
- 还是要复用/清理旧 fallback 路径

---

## 5. 数据模型与持久化落点

核心表定义在：

- `backend/app/db/models.py`

### 5.1 solution 级别

`question_solutions` 当前与可视化相关的字段：

- `visualization_plan_json`
- `visualizations_json`

含义分别是：

- `visualization_plan_json`：Stage 1 规划结果
- `visualizations_json`：前端 resume/展示用的最终序列化结果

### 5.2 row 级别

`visualizations` 表中当前字段已经能同时承载 spec 与 render artifact：

- `viz_ref`
- `title`
- `caption`
- `learning_goal`
- `interactive_hints_json`
- `helpers_used_json`
- `engine`
- `jsx_code`
- `spec_json`
- `ggb_commands_json`
- `ggb_settings_json`
- `params_json`
- `animation_json`

也就是说，当前单条 `VisualizationRow` 同时可以表达：

1. 纯 JSXGraph 实现
2. 纯 GeoGebra 实现
3. 只有 spec、没有可执行产物的 fallback

这为后续优化很重要，因为数据模型已经足够承载“规划-生成-验证-降级”全链路。

---

## 6. 前端渲染架构

### 6.1 组件层

主要文件：

- `frontend/components/VizSandbox.tsx`
- `frontend/components/GeoGebraSandbox.tsx`
- `frontend/components/JsxgraphSandbox.tsx`

当前实际主路径渲染的是：

- `GeoGebraSandbox`

但 `JsxgraphSandbox` 仍保留，供旧数据或旧路径使用。

### 6.2 GeoGebraSandbox 机制

`GeoGebraSandbox.tsx` 做的事情：

1. 用 iframe 加载 `/viz/geogebra-sandbox.html`
2. 等 sandbox 发送 `ready`
3. host 发送 `render` 消息，包含：
   - `ggbCommands`
   - `ggbSettings`
   - `params`
4. sandbox 渲染成功后回传：
   - `metric`
   - `trace`
   - `error`

它还支持：

- 参数更新 `update-params`
- 卸载时 `dispose`
- 没有 `ggbCommands` 时直接回退说明卡片

### 6.3 GeoGebra iframe sandbox

文件：

- `frontend/public/viz/geogebra-sandbox.html`

当前 sandbox 的设计特点：

1. 通过 GeoGebra CDN 加载 `deployggb.js`
2. 只接受 host 的消息协议
3. 根据 `ggbSettings` 推断 `appName` / `perspective`
4. `renderNow` 时：
   - reset / new construction
   - apply settings
   - run commands
   - apply params
   - 输出 metric / ready / trace

5. 还做了一些命令桥接和兼容处理，例如：
   - `SetCoordSystem`
   - `SetAxesVisible`
   - `SetGridVisible`
   - `SetTrace`
   - `SetCaption`
   - `SetConditionToShowObject`

也就是说，前端 GeoGebra sandbox 不只是“执行命令”，还承担了一层：

- **把 LLM 产物映射到 GeoGebra Apps API 可接受表面**

### 6.4 JSXGraph sandbox

文件：

- `frontend/public/viz/sandbox.html`

JSXGraph 路径当前仍然有完整的安全隔离与 tracing：

- 严格 CSP
- `sandbox="allow-scripts"`
- 冻结危险全局对象
- 包装 `requestAnimationFrame`
- trace `initBoard` / `board.create` / render contract
- 校验 `renderVisualization(containerId, spec)` 运行结果

但这条链路目前更像兼容/旧路径支持，而不是当前主可视化链路的默认实现。

---

## 7. “生成可视化”过程中的日志捕获机制

当前日志不是单一来源，而是至少分成两层：

1. **LLM 调用日志**
2. **visual action 日志**

这两层解决的问题不同。

### 7.1 LLM 调用日志

实现文件：

- `backend/app/services/llm_client.py`
- 配置：`backend/app/config.py`

当前配置路径：

- `settings.storage.llm_prompt_log_file`
- `settings.storage.llm_response_log_file`

默认文件：

- `backend/data/logs/llm_prompts.jsonl`
- `backend/data/logs/llm_responses.jsonl`

这层日志记录的是每一次 LLM 调用的：

- `task`
- `prompt_version`
- `model`
- `phase_description`
- `question_id`
- `solution_id`
- `messages`
- `response_schema`
- `response_preview`
- `prompt_tokens`
- `completion_tokens`
- `latency_ms`
- `status`
- `error`

所以在“生成可视化”阶段，它会记录诸如：

- `vizspec`
- `geogebra_codegen`
- 旧路径里的 `vizcoder`
- `vizplanner`
- `vizitem`

这层日志适合回答的问题是：

- 这次调用到底发给模型什么 prompt 了
- token 用了多少
- 返回内容是否触发 repair
- 失败发生在 Stage 1 还是 Stage 2 prompt

### 7.2 visual action 日志

实现文件：

- `backend/app/services/visual_action_logger.py`

配置路径：

- `settings.storage.visual_action_log_file`

默认文件：

- `backend/data/logs/visualActions.jsonl`

这是当前“生成可视化”专用的动作追踪日志，采用 JSONL 追加写入。标准字段包括：

- `timestamp`
- `source`
- `phase`
- `action`
- `status`
- `question_id`
- `solution_id`
- `visualization_id`
- `engine`
- `component`
- `details`
- `error`

这层日志适合回答的问题是：

- 可视化在哪一步失败了
- 是 Stage 1 规划失败、Stage 2 校验失败，还是前端运行失败
- 失败属于 backend / frontend host / sandbox 哪一层

### 7.3 后端如何写 visual action 日志

后端调用统一入口：

- `log_visual_action(...)`

当前主路径中已落地的 backend action 主要包括：

#### Stage 1

- `vizspec.requested`
- `vizspec.failed`
- `vizspec.succeeded`
- `vizspec.persisted`

#### Stage 2 GeoGebra

- `geogebra.codegen.requested`
- `geogebra.codegen.received`
- `geogebra.sanitize.passed`
- `geogebra.validation.passed`
- `geogebra.validation.rejected`
- `geogebra.codegen.failed`

#### 持久化 / 降级

- `visualization.codegen.degraded`
- `visualization.row_persisted`

旧路径中还会记录：

- `jsxgraph.codegen.requested`
- `jsxgraph.validation.passed`
- `jsxgraph.validation.rejected`
- `jsxgraph.repair.requested`

### 7.4 前端如何采集并上报 visual action

host 侧：

- `frontend/components/GeoGebraSandbox.tsx`
- `frontend/components/JsxgraphSandbox.tsx`

当前机制是：

1. host 收到 sandbox 的 `trace`
2. 也会自己记录 host 侧事件
3. 所有事件先进入 `logQueueRef`
4. 满 10 条立即 flush
5. 否则 250ms 定时 flush
6. 通过 `fetch('/api/answer/visual-actions')` 批量 POST
7. 使用 `keepalive: true`

这意味着日志上报是：

- **异步批量**
- **低阻塞**
- **页面卸载时也尽量送达**

host 当前记录的典型事件包括：

- `host.mount`
- `host.unmount`
- `host.render.requested`
- `host.render.skipped`
- `host.dispose.requested`
- `host.param.updated`
- `sandbox.ready`
- `sandbox.metric`
- `sandbox.error`

### 7.5 sandbox 如何产生 trace

GeoGebra sandbox 中：

- `postTrace(action, details, status, error)`

会把 runtime 事件通过 `postMessage` 传回 host。

当前 GeoGebra 侧典型 trace 包括：

- `render.start`
- `render.commands.start`
- `render.commands.finished`
- `render.commands.failed`
- `params.apply.start`
- `params.apply.finished`
- `runtime.metric`
- `runtime.error`
- `render.empty`

JSXGraph sandbox 侧 trace 更细：

- `init_board.start`
- `init_board.ok`
- `board.create.start`
- `board.create.ok`
- `board.create.error`
- `render.contract.checked`
- `render.function.returned`
- `render.invalid_result`
- `render.success`
- `render.exception`
- `sandbox.dispose`

### 7.6 visual action 上报接口

接口在：

- `POST /api/answer/visual-actions`

定义位置：

- `backend/app/routers/answer.py`

请求体是：

- `VisualActionBatch`

返回：

- `{"logged": N}`

后端不会在这里做复杂业务判断，只是把记录批量写入 JSONL。

这点对后续优化有帮助，因为它意味着：

- 现在的 trace 管道已经与核心生成逻辑解耦
- 可以独立扩展字段、增加 trace 点、做离线分析

### 7.7 answer job 对日志的额外利用

`answer_job_service.py` 里还有一个很值得注意的点：

- 当 visualizing 阶段失败时，它会去读 `llm_prompts.jsonl`
- 查找当前 question/solution 最近一次失败的 visualization prompt
- 用它的 `phase_description` 作为更友好的对外报错信息

相关函数：

- `_latest_failed_visualization_phase_description(...)`

这说明当前日志不仅用于排障，也已经开始参与：

- **用户可见错误消息的生成**

---

## 8. 当前实现中的关键差异：设计稿 vs 真实代码

如果只看旧设计，很容易误以为当前系统仍然是：

- Stage 1 选 recommended
- Stage 2 生成 JSXGraph
- 前端 sandbox 执行 JSXGraph

但实际代码已经发生了几个关键变化：

1. **当前主 Stage 2 已经切到 GeoGebra**
2. **VisualizationSpec 已成为一级正式资产，并持久化到 solution**
3. **GeoGebra 失败时支持 spec-only 降级，而不是整题失败**
4. **visual action log 已成为独立日志通道，不再只是普通 `logging`**
5. **旧 `vizcoder`/`vizplanner`/`vizitem` 路径还在，但不再是主入口当前默认路径**

---

## 9. 后续优化时最值得基于现状关注的点

这里只列“从当前代码结构出发”的优化切入点，不展开新方案。

### 9.1 明确主路径与旧路径的边界

当前 repo 里同时存在：

- `vizspec -> geogebra_codegen` 主路径
- `vizcoder -> vizplanner/vizitem` 旧/回退路径

后续优化前最好先决定：

- 继续双轨并存
- 还是把旧路径正式降级为实验/迁移态

### 9.2 把 Stage 1 / Stage 2 / runtime trace 串成同一条分析链

现在日志字段已经有：

- `question_id`
- `solution_id`
- `visualization_id`
- `engine`
- `phase`
- `action`

因此完全可以后续做：

- 每个 visualization 的时间线重建
- stage latency 统计
- 降级率统计
- 失败类别聚类

### 9.3 spec-only fallback 已经是现成抓手

因为当前已经能保留：

- `spec_json`
- `params_json`
- `animation_json`

所以后续可以在“不改数据模型”的前提下继续优化：

- fallback UI
- rerun stage 体验
- 失败后人工修改 spec 再重跑 Stage 2

### 9.4 Stage 1 输入裁剪策略已经开始生效

`summarize_answer_for_visualization(...)` 已经证明这条路是正确的。后续若继续优化性能，优先考虑：

- 进一步精简 Stage 2 prompt 输入
- 对 spec 做更强的字段压缩/标准化
- 使用 visual action + llm prompt logs 评估 token 与失败率的关系

---

## 10. 一句话总结

当前代码库中“生成可视化”的真实主流程已经是：

- **AnswerPackage -> VisualizationSpecBundle -> selected spec -> GeoGebra codegen -> local sanitize/validate -> VisualizationRow -> GeoGebraSandbox 渲染**

而“生成可视化”过程中的日志也已经形成两条独立但互补的链路：

- **LLM prompt/response JSONL：记录模型调用本身**
- **visualActions JSONL：记录从后端 stage 到前端 runtime 的动作级 trace**

这套结构已经足够支撑下一轮围绕性能、稳定性、降级体验、失败分析的优化工作。

---

## Latest Design: 生成可视化稳定化与质量优化方案

### 1. 本次日志结论

从 `backend/data/logs` 最近记录看，`question_id=63cc2385-bb94-4d4c-a0c8-3ca0bcaabe84` 在“生成可视化”阶段的真实路径是：

1. Stage 1 `vizspec.requested` 于 `2026-04-24T00:51:48Z` 开始。
2. Stage 1 第一次结构化输出曾因 `VisualizationSpecBundle allows only one recommended visualization` 触发 repair。
3. repair 后 Stage 1 成功，产出 2 个 specs：
   - `viz_k_translation_definition`
   - `viz_k_minmax_exploration`
4. 系统选择 `viz_k_translation_definition` 作为推荐项并持久化。
5. Stage 2 `geogebra_codegen` 生成了较大的 GeoGebra payload：
   - `commands=28`
   - `property_commands=33`
   - `interaction_objects=1`
   - `script_needed=false`
6. 后端 sanitize 通过，说明 Pydantic payload 与确定性命令扫描没有拦截它。
7. runtime validator 启动 Node/Playwright 时崩溃：
   - 缺少 `/Users/jianbo/Library/Caches/ms-playwright/chromium_headless_shell-1217/...`
   - Playwright 提示需要执行 `npx playwright install`

因此，这次不能完成“生成可视化”的直接原因不是 GeoGebra 命令本身已被证明错误，而是 **runtime validator 的浏览器二进制缺失**。更深层的问题是：当前代码把 validator 基础设施故障作为 `RuntimeError` 抛出，而不是作为“验证能力不可用”的可降级状态处理，导致 `answer_job_service` 的候选 spec fallback 逻辑没有机会继续执行，最终整条 answer job crash。

### 2. 问题分层

#### 2.1 立即故障：后端同步浏览器 validator 不适合主链路

`backend/viz_validator/geogebra_validate.mjs` 在后端同步启动浏览器来验证 GeoGebra payload。当前机器缺少对应浏览器二进制，所以 validator 直接退出并让 Python 后端抛出 `RuntimeError`。

设计要求：

- 不再把后端浏览器 validator 放在用户等待链路中。
- validator 环境问题不能 crash answer job。
- 日志要能明确区分“payload 静态校验失败”和“前端真实渲染失败”。

#### 2.2 上游警告：vizspec 多推荐项

console 中的 `VisualizationSpecBundle allows only one recommended visualization` 表示 Stage 1 第一次输出把多个可视化都标成了 `recommended=true`。`llm_client` 后续 repair 成功，所以这不是本次最终 crash 的直接原因，但它浪费了一次 LLM 修复调用，并增加延迟。

设计要求：

- Prompt 和 schema 要继续保持“最多一个 recommended”。
- 服务层可以在 repair 前做可解释的 deterministic normalization：若只有这个错误，保留 priority 最小且 readiness 最高的 recommended，其余改为 false，再进入 Pydantic 终验。
- 日志中标记 `vizspec.normalized_recommendation`，避免把可自动修正的小格式问题算作模型质量失败。

#### 2.3 Stage 2 产物复杂度过高

当前 payload 为 28 条创建命令和 33 条属性命令，且包含 step slider、条件对象、文本、隐藏辅助对象。对“新定义几何”来说，这种全自动构造教学价值高，但非常容易踩 GeoGebra Apps API 的解析差异，例如：

- `O + vector`、`B - A`、`UnitVector(...)` 这类向量表达式在 Apps API 中比点坐标显式表达更脆弱。
- `If(step >= n, Segment(...))` 会生成大量条件对象，后续 property command 可能引用尚未稳定存在的对象。
- 过多 `SetVisibleInView` 和 `SetCaption` 增加了 runtime failure 面。

设计要求：

- 默认生成“核心可解释图”，而不是一次性生成完整动画剧场。
- 把高风险构造降级为静态关键状态或少量 step。
- 将命令复杂度作为 Stage 2 质量门槛，而不是只看 schema 是否通过。

### 3. 新目标

“生成可视化”阶段的目标应调整为：

- **Always produce an inspectable visualization asset**：至少有 spec-only 或 static fallback，不因 validator 环境问题中断整题。
- **Prefer stable math clarity over rich animation**：默认选择最稳定的可解释图，复杂动画需要明确收益和通过更强验证。
- **Remove backend browser runtime validation from the product path**：浏览器级 validator 成本高、环境脆弱，不应成为用户等待链路里的必需步骤。
- **Make validation state observable**：把 static validation 与 frontend render feedback 分成不同状态。
- **Use fallback candidates effectively**：任何单个 spec、单个 engine、单个 validator 的失败都不能直接跳出候选循环。

### 4. 后端设计

#### 4.1 Design Fix：移除后端浏览器 validator

当前 `backend/viz_validator/geogebra_validate.mjs` 会在后端启动真实浏览器，加载 `frontend/public/viz/geogebra-sandbox.html`，再执行 GeoGebra payload。这能捕获真实浏览器里的 Apps API 错误，但代价过高：

- 每次 visualizing 都要启动浏览器或 browser page，增加用户等待时间。
- 依赖本机浏览器二进制，开发机、CI、Docker、生产环境都容易缺失或版本不匹配。
- GeoGebra CDN / applet 初始化也会引入额外不稳定性。
- validator 环境失败与 payload 失败混在一起，容易误判根因。
- 对主流程来说，浏览器级校验的收益不足以抵消同步阻塞成本。

新设计：

- 后端主流程删除或禁用 `geogebra_validate.mjs` 这类浏览器 runtime validator 调用。
- `validate_geogebra_execution_payload(...)` 的默认实现改为 static validator：
  - Pydantic schema 校验。
  - command/property command 数量预算。
  - object definition/reference graph 校验。
  - GeoGebra anti-pattern guard。
  - 参数与 interaction object 对齐校验。
  - command 字符串基础语法与危险模式扫描。

默认用户路径应是：

1. LLM 生成 GeoGebraExecutionPayload。
2. 后端 sanitizer + static validator。
3. static pass 后立即持久化并返回前端。
4. 前端 GeoGebraSandbox 尝试真实渲染并上报 trace。
5. 若前端 runtime 失败，再显示 fallback，并把错误写入 visual action log。

#### 4.2 把 validator 环境失败改成可降级结果

在过渡期内，如果仍有旧代码路径调用浏览器 validator，必须把它视为可降级问题，而不是 answer job 崩溃源。

新增或保留异常类型：

- `GeoGebraValidatorUnavailable`

处理规则：

- `GeoGebraValidationError`：payload 本身不合格，可尝试 LLM repair 或下一个 spec。
- `GeoGebraValidatorUnavailable`：旧浏览器 validator 被误调用或环境不可用；不做 repair，直接返回 degraded/static-only result。
- 非预期异常：记录 `geogebra.codegen.failed_unexpected`，但包裹为 `GeoGebraCodegenResult(execution_payload=None, error_summary=...)`，让 answer job 继续走 spec-only fallback。

`generate_geogebra_visualization_or_fallback(...)` 应保证：

- 不向上抛 validator 基础设施错误。
- 返回值永远表达三种之一：
  - `static_passed`
  - `degraded_validator_removed_or_unavailable`
  - `failed_payload_invalid`

#### 4.3 answer_job_service 候选循环兜底

当前 `answer_job_service` 只有在 `generate_geogebra_visualization_or_fallback` 正常返回 `execution_payload=None` 时才会记录 `attempt_errors` 并继续。如果内部抛 `RuntimeError`，循环被打断。

改造为：

- candidate 调用外层加窄范围 `try/except Exception`。
- 捕获后写入 `attempt_errors[candidate_spec.id]`。
- 继续尝试下一个 candidate。
- 所有 candidate 失败后进入现有 spec-only fallback。

这层兜底不能替代 service 内部兜底，但可以作为最后防线，避免任何单个候选导致整题 crash。

#### 4.4 Stage 2 validation state

引入验证状态：

- `static_passed`: 后端静态校验通过，已返回前端尝试真实渲染。
- `static_failed`: 后端静态校验失败，进入 repair、下一个 candidate 或 spec-only fallback。
- `frontend_runtime_passed`: 前端 GeoGebraSandbox 已真实渲染成功并上报。
- `frontend_runtime_failed`: 前端真实渲染失败，显示 fallback 并记录 trace。

VisualizationRow 建议增加或在 `spec_json/execution_payload_json` 中记录：

- `validation_status`
- `validation_error_kind`
- `validation_error_message`
- `frontend_runtime_trace`

前端据此展示“已通过静态校验”“真实渲染失败，已降级”等轻量状态，避免把未验证产物伪装成完全可靠。

#### 4.5 Design Fix：小错误容忍与部分执行

最新日志显示，LLM 生成的 GeoGebra payload 常见失败不是数学构型完全错误，而是小的结构化输出错误：

- `optional_script.needed=false` 时仍输出 `script_type=""` 或 `trigger=""`。
- 把 GeoGebra 数学点 `P`、`Q`、`C` 误放入 `interaction_objects`，并标成 `type="point"`。
- 使用 `C = A + (cos(beta), sin(beta))` 这类 GeoGebra Apps API 中不够稳定的点加向量简写。
- `property_commands` 比预算多 1-2 条，但只是标签、颜色、坐标等非数学核心命令。

新设计把这些问题从“整张图失败”降级为“可规范化的小错误”：

- Schema 层容忍 harmless enum mistakes：
  - 空 `optional_script.script_type` 自动归一为 `"none"`。
  - 空 `optional_script.trigger` 自动归一为 `"none"`。
  - 点、线、圆、轨迹等非 UI interaction type 自动降级为 `"none"`，不阻塞 schema 解析。
- Sanitizer 层做 deterministic rewrite：
  - Greek slider 名称如 `alpha`、`beta` 改写为 `param_alpha`、`param_beta`。
  - `A + (dx, dy)` 改写为 `(x(A)+dx, y(A)+dy)`。
  - 超出预算的 property commands 直接裁剪到 16 条，优先保留前面的可视化属性。
- Static validator 仍保持严格：
  - creation commands 超预算仍失败。
  - expected object 引用不存在仍失败。
  - optional script、条件对象创建、危险命令仍失败。
- 前端 GeoGebraSandbox 改为：
  - creation command 失败仍 hard fail。
  - expected object 缺失仍 hard fail。
  - text/label/caption 这类非核心 expected object 缺失只记录 degraded trace，不再阻止几何图展示。
  - property command 失败只记录 degraded trace，不再阻止已创建的数学对象展示。

这一层的目标不是放松数学正确性，而是避免“标签/枚举/样式层小错”导致整个可视化退回规格卡片。

#### 4.6 GeoGebra 错误分级：fatal vs tolerable

从最新运行日志看，GeoGebra 内容错误需要分级处理，不能都按“整图失败”处理。

Fatal problems，必须在生成/后端校验阶段避免或修复：

- 核心 creation command 无法创建对象，例如点、圆、线段、交点、数值测量不存在。
- 后续命令依赖前面未稳定命名的对象，例如 `P_prime_1` 不存在导致 `k`、距离线段、最终结论都无法计算。
- 使用 GeoGebra Apps API 不稳定的命名方式，例如：
  - `pts = Intersect(c1, c2)` 让 GeoGebra 自动创建 `pts_{1}` / `pts_{2}`，但没有稳定对象 `pts`。
  - 再使用 `P1 = Element(pts, 1)`，浏览器里可能不会创建 `P1`。
- expected core objects 缺失，例如 `P_prime_1`、`P_prime_2`、核心线段、核心圆、核心数值 `k`。
- 条件对象创建或对象类型错误导致依赖链断裂。

Fatal 类问题的优化策略：

- Prompt 明确禁止 `tmp=Intersect(c1,c2)` + `Element(tmp,n)`，要求直接写 `P1=Intersect(c1,c2,1)`、`P2=Intersect(c1,c2,2)`。
- 后端 sanitizer 对该模式做 deterministic rewrite，并删除不稳定的临时交点列表命令。
- Static validator 保持 core expected object 严格校验。
- 前端对 creation command failure 和 core expected object 缺失继续 hard fail。

Tolerable problems，可以保持浏览器继续运行：

- text/label/caption/dynamic text 没有稳定 `exists(name)`，例如 `k_text`。
- property command 失败，例如颜色、线宽、隐藏辅助对象、标签显示失败。
- 非核心 display/annotation object 缺失。
- LLM 输出的无害 enum 小错，例如 `optional_script.script_type=""` 但 `needed=false`。

Tolerable 类问题的优化策略：

- 后端不把 text/label/caption 放入 hard `expected_created_objects`。
- 前端对 text/label/caption missing 只记录 degraded trace。
- 前端对 property command failure 只记录 degraded trace，不阻止已创建的核心几何展示。
- Schema/sanitizer 对无害枚举和 UI metadata 错误做归一化。

### 5. Stage 1 设计优化

#### 5.1 推荐项归一化

Stage 1 repair 成功说明 schema 约束有效，但为了减少延迟，可以加 deterministic normalization：

规则：

1. 若 `visualizations` 为 1 个，强制该项 `recommended=true`，除非 `overall_readiness=needs_revision`。
2. 若多个 `recommended=true`：
   - 按 `overall_readiness`、`implementation_stability_score`、`priority` 排序。
   - 保留第一项 recommended。
   - 其余改为 false。
3. 若无 recommended 且存在 ready/mostly_ready：
   - 选择最稳定项 recommended=true。
4. 若全部 needs_revision：
   - 允许无 recommended，但 answer job 应直接 spec-only 并提示需要人工调整。

已实现的 Stage 1 design fix：

- `VisualizationSpecBundle` 在严格校验前会把多个 `recommended=true` 归一为一个。
- 选择规则优先看 `overall_readiness`，再看 `priority`，再看 `implementation_stability_score`。
- 若 LLM 输出超过 2 个候选，只保留前 2 个，避免候选数量错误中断流程。
- `recommended_command_families` 接受 `locus`，因为轨迹类 GeoGebra 规格会自然使用 Locus/轨迹构造。
- 如果 LLM 把静态 fallback 的对象仍标成 `moving_point` / `traced_object`，但 `has_animation=false`，后端会把这些对象降级为普通 `point`，并在无参数时清掉 `requires_slider`，避免元数据小错导致 Stage 1 整体失败。

#### 5.2 选择策略从“教学价值优先”改为“双分排序”

当前推荐项可以是教学价值最高但实现复杂的图。新策略：

- 第一分：教学必要性。
- 第二分：实现稳定性。
- 默认进入 Stage 2 的是 `teaching_value=high` 且 `implementation_stability_score>=85` 的最简图。
- 如果一个图需要复杂 animation，但另一个图是静态核心关系图，先生成静态核心关系图。

对本题这类“k-平移距离新定义”，优先图应是：

- 固定 A、B、圆 O。
- 显示中点 M、两条可能弦的中点 M1/M2、两条平移距离。
- 直接标注 `k=max(d1,d2)`。

不应默认做：

- 多 B 点 selector。
- 完整 step animation。
- 动态隐藏/显示大量条件对象。

### 6. Stage 2 生成优化

#### 6.1 命令复杂度预算

给 `GeoGebraExecutionPayload` 增加推荐预算：

- 初版主路径：`commands <= 16`
- `property_commands <= 16`
- `interaction_objects <= 1`
- 默认 `optional_script.needed=false`
- 默认不使用 `If(step >= ..., Object(...))` 创建条件对象

超预算时：

- 先走 deterministic simplifier，删除非关键 styling。
- 如果仍超预算，让 LLM repair 为 static fallback。
- 不把复杂 payload 直接交给前端渲染。

#### 6.2 稳定命令白名单

Stage 2 prompt 和 sanitizer 应偏向 GeoGebra Apps API 最稳定子集：

- 点：`A=(x,y)`
- 圆：`c=Circle(O,r)`
- 线段：`s=Segment(A,B)`
- 中点：`M=Midpoint(A,B)`
- 距离：`d=Distance(A,B)`
- 文本：少量 `Text(...)`
- 样式：少量 `SetColor`, `ShowLabel`, `SetCaption`

尽量避免：

- 点加向量表达式：`A + v`
- `Vector(B - A)` 这种嵌套差向量
- 大量 `If(..., Segment(...))`
- 对条件对象立即执行 property command
- 名称含 `'`、中文、复杂下划线组合

#### 6.3 本题推荐的可视化实现形态

本题最好的默认可视化不是完整求解动画，而是“定义公式可视化”：

- 固定圆 `O` 和线段 `AB`。
- 构造 `M=Midpoint(A,B)`。
- 画过 `O` 且垂直于 `AB` 的方向线。
- 在该方向线上标出两个可能的弦中点 `M1/M2`。
- 显示两条候选弦和两条平移距离。
- 标注 `k` 是较长平移距离。

如果 GeoGebra 构造两条精确弦不稳定，fallback 采用“公式解释图”：

- 不强制画出所有精确 chord endpoint。
- 画出中心、原中点 M、两个候选中点 M1/M2、距离段。
- 文字说明 `P'Q'` 是与 `PQ` 平行且等长的弦。

这样仍能解释关键概念，而且命令数量更少，稳定性更高。

### 7. 前端与用户体验

前端需要区分三种状态：

- `static_passed`: payload 已通过后端静态校验，前端将尝试真实渲染。
- `frontend_runtime_failed`: 前端发现真实渲染失败，显示 fallback。
- `spec_only`: 没有可执行 payload，展示 Stage 1 规格说明和可重试入口。

用户在“生成可视化”阶段不应看到整题失败。可视化失败时状态应是：

- 解答仍可确认。
- 可视化卡片显示降级原因。
- 提供“重新生成可视化”操作。

### 8. 日志设计

新增或规范化 action：

- `geogebra.static_validation.passed`
- `geogebra.static_validation.failed`
- `geogebra.codegen.browser_validator_removed`
- `geogebra.codegen.degraded_static_failed`
- `frontend.geogebra.runtime_failed`
- `frontend.geogebra.runtime_passed`
- `vizspec.normalized_recommendation`
- `visualization.candidate.failed`
- `visualization.spec_only_persisted`

日志分类必须清楚：

- `schema_error`: LLM JSON 不符合 Pydantic。
- `payload_static_error`: 本地 sanitizer/static guard 发现 GeoGebra 风险。
- `frontend_runtime_error`: 前端 GeoGebra sandbox 执行命令失败。
- `removed_backend_browser_validator`: 旧后端浏览器 validator 路径被移除或禁用。
- `timeout`: LLM 或 runtime 超时。

这可以避免之后排查时把“后端浏览器环境问题”误判为“模型不会生成 GeoGebra”。

### 9. 实施顺序

1. **Design fix：移除后端浏览器 validator**
   - 默认 visualizing 不再调用 `geogebra_validate.mjs`。
   - `validate_geogebra_execution_payload(...)` 改为 static validator。
   - README 中标明“生成可视化”主流程不需要后端浏览器 runtime validator。

2. **后端防 crash**
   - 过渡期内把旧浏览器 validator 的 `RuntimeError` 拆成 `GeoGebraValidatorUnavailable`。
   - `generate_geogebra_visualization_or_fallback` 保证不向 answer job 抛出 validator 环境错误。
   - answer job candidate loop 增加最后兜底。

3. **降级资产完善**
   - 持久化 `validation_status` 与 `validation_error_kind`。
   - 前端显示 `static_passed/frontend_runtime_failed/spec_only` 状态。

4. **质量优化**
   - Stage 1 推荐归一化。
   - Stage 2 prompt 加命令预算与稳定命令白名单。
   - 对高复杂 payload 先简化，再走 static validator；仍高风险则进入 spec-only fallback。

5. **验证**
   - 单元测试：默认路径不调用 `geogebra_validate.mjs`。
   - 单元测试：旧浏览器 validator 被误调用时不 crash，仍保留 static-passed 或 spec-only row。
   - 单元测试：多个 recommended 自动归一化或 repair 后成功。
   - 集成测试：static validator 能拦截高风险命令与错误引用。
   - 前端测试：GeoGebraSandbox runtime 失败时显示 fallback 并记录 trace。
   - 日志测试：每类失败都有稳定 action 和 error kind。

### 10. 成功标准

这轮优化完成后，应满足：

- 默认生成链路不需要后端浏览器 validator。
- 旧后端浏览器 validator 缺失或被误调用时，“生成可视化”不会 crash answer job。
- 用户至少能看到 spec-only 可视化说明。
- 日志能一眼看出是后端静态校验失败、旧 validator 路径误调用，还是前端真实渲染失败。
- Stage 1 多 recommended 不再造成明显延迟或失败。
- Stage 2 默认产物更小、更稳定，复杂动画只作为增强路径。
- 本题这类高难“新定义 + 最值”问题优先生成核心关系图，而不是高风险全步骤动画。

---

## 11. OpenAI 默认 Provider 迁移设计

### 11.1 目标

本轮迁移把默认 LLM provider 从 Gemini 切到 OpenAI 兼容接口，同时保留 Gemini 作为可选 provider。

默认行为：

- 生成类调用默认走 OpenAI Responses API。
- embedding 默认走 OpenAI Embeddings API。
- Gemini 仍可通过配置切回。
- OpenAI `api_key` 从 `OAI_API_KEY` 读取。
- OpenAI `base_url` 从 `OAI_BASE_URL` 读取。
- 不做 OpenAI 失败后的 Gemini 自动 fallback，避免隐藏双重成本和不确定行为。

### 11.2 配置设计

新增 `[openai]` 配置段：

```toml
[openai]
model_default  = "gpt-5.4-pro"
model_parser   = "gpt-5.4-pro"
model_solver   = "gpt-5.4-pro"
model_vizcoder = "gpt-5.4-pro"
model_chat     = "gpt-5.4-pro"
model_embed    = "text-embedding-3-large"
embed_dim      = 1536
```

环境变量：

```bash
export OAI_API_KEY="..."
export OAI_BASE_URL="https://api-xai.ainaibahub.com/v1"
```

Provider 选择：

```toml
[llm]
provider = "openai"  # "openai" | "gemini"

[retrieval]
embedder = "openai"  # "openai" | "gemini" | "bge-m3"
```

模型切换规则：

- `settings.llm_model("parser")`
- `settings.llm_model("solver")`
- `settings.llm_model("vizcoder")`
- `settings.llm_model("dialog")`

业务服务不再直接读 `settings.gemini.model_*`，而是通过 active model resolver 获取当前 provider 的模型。

### 11.3 后端 Provider 边界

LLM gateway 变成 provider-neutral：

- `LLMTransport`：结构化 JSON、文本、流式 JSON、embedding 的统一协议。
- `LLMClient`：保留现有 repair、Pydantic validation、prompt logging、cost ledger、stream parser。
- `GeminiClient` / `GeminiTransport` 暂时作为兼容 alias 保留，避免一次性改动所有测试和历史调用点。

Transport 实现：

- `OpenAIResponsesTransport`
  - `responses.create(...)` 处理结构化 JSON、文本、多模态 parser、流式 solver/vizcoder。
  - `embeddings.create(...)` 处理 OpenAI dense embedding。
- `GoogleGeminiTransport`
  - 保留原有 Gemini generation 和 Gemini embedding 行为。

依赖装配：

- `get_llm_client()` 根据 `[llm].provider` 选择 OpenAI 或 Gemini transport。
- 不实现自动 fallback；失败直接按当前错误处理链路返回。

### 11.4 OpenAI 调用形态

结构化输出：

- 继续使用现有 prompt schema。
- Responses API 使用 JSON Schema format。
- 后端仍用 Pydantic 做最终校验。
- 校验失败仍进入现有 repair loop。

多模态 parser：

- 现有 parser prompt 仍生成 `parts`。
- OpenAI transport 将：
  - text part 转为 `input_text`
  - inline image base64 转为 data URL `input_image`

流式输出：

- OpenAI streaming 读取 `response.output_text.delta`。
- delta 继续交给现有 `TopLevelStreamParser`。
- 前端 SSE 行为不需要重写。

Embedding：

- 默认模型 `text-embedding-3-large`。
- 默认 `dimensions=1536`，与当前 Gemini 1536 维设置对齐。
- transport 对返回向量做 L2 normalize，保持 Milvus IP 检索语义稳定。
- 如果未来修改 `openai.embed_dim`，现有 Milvus dim mismatch bootstrap/reindex 机制负责重建 dense collections。

### 11.5 前端与 Admin

`/api/admin/config` 需要展示：

- active LLM provider
- OpenAI key 是否配置
- OpenAI base URL 是否配置
- OpenAI per-task models
- Gemini 备用配置
- active dense dim

前端展示文案应从硬编码 `Gemini` 改为：

- `OpenAI`，当后端明确返回 provider label 时；
- 或通用 `LLM`，用于不需要暴露具体 provider 的进度/锁定提示。

### 11.6 验证标准

完成后应满足：

- 默认配置下 generation 调用使用 OpenAI `gpt-5.4-pro`。
- 默认配置下 embedding 使用 OpenAI `text-embedding-3-large`，1536 维。
- 设置 `[llm].provider="gemini"` 后，parser/solver/vizcoder/dialog 可切回 Gemini。
- 设置 `[retrieval].embedder="gemini"` 后，dense embedding 可切回 Gemini。
- OpenAI 失败时不会自动调用 Gemini。
- 现有 prompt logging、cost ledger、repair loop、solver streaming、visualization generation 链路继续工作。

---

## New Opt 04.25

### 1. 本次重新分析后的判断

当前代码已经实现了上一轮很多关键稳定化措施：

- `geogebra_codegen_prompt.py` 已经限制 `commands <= 16`、`property_commands <= 16`。
- `geogebra_validator.py` 已经是后端 static validator，不再在主链路启动浏览器。
- sanitizer 已经能改写 Greek/reserved name、`A+(dx,dy)`、`Intersect list + Element`。
- `answer_job_service.py` 和 `answer.py` 已经有候选 spec 循环，单个候选异常不会直接 crash 整个 visualizing。
- `GeoGebraSandbox` 已经把 property command failure 降级，不再因为颜色/标签失败让整图失败。

所以现在最值得继续改的，不是继续扩大后端静态规则，而是解决两个剩余问题：

1. **静态通过但前端真实渲染为空**：GeoGebra Apps API 可能对某条 creation command 静默失败，`evalCommandGetLabels` 返回空或自动改名；如果 `expected_created_objects` 不完整，前端仍可能发出 metric，看起来成功但画布没关键对象。
2. **Gemini Stage 2 缺少可执行样例约束**：当前 prompt 规则很多，但没有“最小稳定 GeoGebra payload”的 few-shot。Gemini 容易知道不能做什么，却不知道应该优先交什么形态。

本轮优化目标应从“后端再多挡一些字符串”调整为：

- **每次 Stage 2 至少生成一个可见、可检查、可降级的核心图**。
- **前端必须能识别空图 / 半空图，而不是只看命令执行是否抛错**。
- **GeoGebra payload 要按“核心层 + 增强层”设计，让增强层出错时核心图仍能显示**。
- **默认不依赖多次 LLM retry 修复 GeoGebra**。retry 成本高，而且 GeoGebra Apps API 的失败经常是运行时语义问题，不一定能靠再生成一次稳定解决；主优化应放在一次生成后的本地规范化、分层执行和部分容错。

### 2. Opt A：增加最小可见对象合同

#### 问题

`GeoGebraExecutionPayload.expected_created_objects` 现在完全由 LLM 输出。后端只检查：

- expected name 是否出现在 `commands[]` 左侧定义中；
- 前端 runtime 再检查 expected object 是否真实存在。

但如果 Gemini 少填 expected object，或者把核心对象误判成 text/annotation，可能出现：

- creation command 实际没创建任何对象；
- expected list 为空或只包含少量非核心对象；
- 前端没有 hard fail；
- 用户看到空白 GeoGebra 面板。

#### 设计

新增一个确定性的 `core_object_contract`，不要只信 LLM 自报的 expected list。

后端 sanitizer/static validator 增加：

- 从 `commands[]` 左侧抽取所有创建名。
- 根据 command head / name / expected role 估算核心对象：
  - 点、线段、直线、圆、函数、交点、数值测量都算 core。
  - Text、caption、label、helper、hidden auxiliary 不算 core。
- 若 `expected_created_objects` 为空，则自动补入前 3-6 个 core LHS。
- 若 `expected_created_objects` 全是 soft object，则 static failed，优先本地补 core expected 或降级为 deterministic static fallback，不默认要求 LLM repair。
- 每个 payload 至少应有：
  - `min_core_expected_objects >= 2`
  - `min_visible_math_objects >= 2`
  - 若有 measurement_demo，则至少一个 numeric/segment measurement object。

前端 `geogebra-sandbox.html` 增加 runtime 检查：

- 每条 creation command 如果有明确 LHS，例如 `P=...`，执行后必须满足 `ggbApi.exists("P")`。
- 如果 `evalCommandGetLabels(cmd)` 返回空，而且 LHS 不存在，先根据 command tier 决定是否继续：
  - core tier 失败：记录 `payload.create.step.core_failed`，进入核心 fallback 或整图 fallback。
  - optional tier 失败：记录 `payload.create.step.optional_failed`，跳过该对象，继续显示核心图。
- render 结束后调用 `getAllObjectNames()` 或等价 API 统计对象数。
- 如果对象数为 0，记录 `render.empty_runtime` 并 hard fail。
- 如果 core expected objects 缺失，继续 hard fail；soft text/caption 缺失仍 degraded。

这样能挡住“命令没抛异常，但实际什么都没画”的情况。

### 3. Opt B：Stage 2 使用 lenient draft，再升级 strict payload

#### 问题

`generate_geogebra_visualization_or_fallback(...)` 当前直接让 `llm.call_structured(..., model_cls=GeoGebraExecutionPayload, disable_repair=True)`。

这意味着很多可自动修的小错误会在进入 sanitizer 前就被 Pydantic 拦掉，例如：

- `commands` 偶尔输出成字符串数组，而不是 `{step,purpose,command}` 数组。
- `step` 输出成 `"1"`。
- `optional_script` 缺少部分空字段。
- `execution_mode` 输出接近但不完全等于 `command_only`。
- `expected_created_objects` 遗漏或字段名小错。

现在只有 schema rejection 后直接 degraded，没有针对这类“可规范化结构错误”的本地修复机会。

#### 设计

新增 `GeoGebraExecutionPayloadDraft`，类似旧路径里的 `VisualizationDraft`：

- 字段更宽松：
  - `commands: list[str | dict]`
  - `property_commands: list[str | dict]`
  - `optional_script: dict | None`
  - `expected_created_objects: list[str | dict]`
  - `execution_mode` 允许空值或近似值。
- 新增 `normalize_geogebra_execution_payload_draft(...)`：
  - string command 自动包成 `{step, purpose, command}`。
  - string expected object 自动包成 `{name, type:"unknown", role:"core"}`。
  - 缺失 optional_script 自动补 `needed=false/script_type=none/trigger=none`。
  - step 自动重排为 1..N。
  - 仍然禁止 JavaScript、optional script、危险命令。
- normalization 后再进入现有 `GeoGebraExecutionPayload.model_validate(...)` 和 static validator。

Stage 2 调用改成：

1. LLM 输出 draft。
2. deterministic normalization。
3. strict model validation。
4. sanitizer。
5. static validator。
6. 如果失败，优先本地降级为 spec-only 或 deterministic static fallback；不默认追加 LLM repair。

收益：

- 减少 Gemini 因 JSON 小形状错误导致的 spec-only fallback。
- sanitizer 能处理更多“本来可救”的 payload。
- 真正数学/GeoGebra 风险仍由 strict validator 拦截。
- 控制 API 成本：一次 Stage 2 调用应尽量产出可部分运行的 payload，而不是依赖多轮 regeneration。

### 3.1 Opt B2：命令分层，让部分错误不拖垮整图

#### 问题

当前 `commands[]` 是一条线性创建序列。前端虽然已经把 `property_commands` 失败降级，但 creation command 失败仍基本按整图失败处理。对 GeoGebra 来说，这太脆弱：

- 核心点/线/圆失败确实应该失败。
- 辅助线、文本、额外测量、第二种 case、装饰对象失败，不应该让已创建的核心图消失。
- LLM 最容易错的往往是增强对象，而不是最基础的点、圆、线段。

#### 设计

给 Stage 2 payload 增加轻量分层语义。可以先不改数据库，用 command step 的 `purpose` 或新增字段表达：

- `tier="core"`：没有它就无法解释本图，例如关键点、圆、函数、主线段、主测量。
- `tier="support"`：辅助理解，例如辅助线、第二组比较对象、额外测量。
- `tier="annotation"`：文本、标签、caption、颜色、隐藏 helper。

如果短期不改 schema，可以约定 `purpose` 前缀：

- `[core] Create point A`
- `[support] Create auxiliary line l`
- `[annotation] Add explanatory text`

后端 prompt 明确要求：

- 前 5-8 条 command 必须构成完整 core diagram。
- support/annotation 必须只依赖已经存在的 core objects。
- 不允许 core command 依赖 annotation/support object。
- expected_created_objects 只放 core + 必要 support，不放 annotation。

前端执行策略：

- 先执行 core commands。
- core 全部存在后立即可以展示。
- support command 失败只 degraded，不清空 core。
- annotation command 失败只 trace，不影响展示。
- property command 失败继续保持 degraded。

这样即使 GeoGebra 内容有部分错误，学生仍能看到最重要的核心图。

### 4. Opt C：给 GeoGebraCodegenPrompt 加稳定 few-shot

#### 问题

当前 `geogebra_codegen_prompt.py` 有规则和 cheatsheet，但没有正向 few-shot。Gemini 在复杂几何题里容易追求完整动画，或者输出大量 styling/condition/text，最后虽然看似符合规则，但前端实际运行风险高。

#### 设计

在 `GeoGebraCodegenPrompt.fewshot_examples()` 增加 2-3 个很小的 payload 示例：

1. **静态几何测量图**
   - `O=(0,0)`
   - `A=(3,0)`
   - `c=Circle(O,A)`
   - `P=(4,2)`
   - `d=Segment(P,A)`
   - 只包含少量 `SetColor/SetLineThickness/ShowLabel`。

2. **函数图 + 单滑块**
   - `a=Slider(-3,3,0.5)`
   - `f(x)=a*x^2`
   - `V=Extremum(f)`
   - `interaction_objects=[{name:"a", type:"slider"}]`
   - expected objects 包含 `a,f,V`。

3. **交点直接命名示例**
   - `P1=Intersect(c1,c2,1)`
   - `P2=Intersect(c1,c2,2)`
   - 明确不要 `pts=Intersect(...)` + `Element(pts,1)`。

few-shot 中刻意保持：

- `commands <= 8`
- `property_commands <= 6`
- no optional script
- expected list 只放 core objects
- 中文 title/summary，英文 GeoGebra object names

这比继续堆禁止规则更有效，因为模型会模仿“短、小、稳定”的输出形态。

### 5. Opt D：Stage 1 到 Stage 2 之间增加风险门控

#### 问题

`select_recommended_visualization(...)` 当前排序仍偏向 `priority`，然后才看 `implementation_stability_score`。候选循环可以在 static failure 后换备用 spec，但如果主候选 static pass、runtime 却空图，后端已经持久化，备用候选不会再尝试。

#### 设计

在进入 Stage 2 前增加 `estimate_geogebra_runtime_risk(spec)`：

高风险信号：

- `requires_locus=true`
- `requires_sequence_or_list_generation=true`
- `requires_minimal_script=true`
- `has_animation=true` 且 parameters 超过 1 个
- visualization_type 是 `construction_steps` 但 animation_sequence 很长
- visible/highlighted objects 超过 10 个
- `implementation_stability_score < 85`
- `fallback_if_animation_is_too_complex` 明确可用

处理规则：

- 若主 spec 高风险但有低风险备用 spec，优先用低风险 spec 做 Stage 2。
- 若必须使用高风险 spec，则给 Stage 2 prompt 传入 `force_static_fallback=true`。
- `force_static_fallback=true` 时，prompt 明确要求：
  - 不做动画；
  - 不做条件对象；
  - 不做 sequence/list/locus；
  - 只实现 `implementation_guidance.fallback_if_animation_is_too_complex`。

持久化中保留：

- `selected_visualization_id`: 教学上选中的 spec。
- `executed_visualization_id`: 实际生成 GeoGebra payload 的 spec。
- `stage2_risk_reason`: 为什么改用 fallback 或备用 spec。

这样可以避免“教学上最完整但运行上最脆弱”的 spec 抢占默认渲染。

### 6. Opt E：前端 runtime 部分容错，而不是默认二次生成

#### 问题

现在 `/api/answer/visual-actions` 只是写 JSONL。当前端 `payload.create.error`、`expected_objects.check.error`、`render.empty_runtime` 发生时，系统主要显示 fallback 卡片。

但默认自动调用 LLM 再生成一次不是优先方案：

- API 成本会放大，尤其 visualizing 已经在 solver 后面，用户等待时间也会增加。
- GeoGebra runtime failure 经常不是 prompt 没写清，而是 Apps API 对某种命令组合不稳定。
- retry 可能生成另一套更复杂 payload，反而引入新的失败。
- 对用户来说，看见核心图比等待一次不确定的完整重生成更有价值。

#### 设计

把 runtime trace 的第一用途改成**局部降级与可观测性**，不是默认 repair。

1. `GeoGebraSandbox` 对每个 command 记录：
   - `action=frontend.geogebra.runtime_failed`
   - `error_kind=create_failed | expected_missing | empty_render | optional_failed | property_failed`
   - `tier=core | support | annotation`
   - `failed_command`
   - `missing_expected_objects`
   - `created_labels`
   - `object_count`
2. sandbox 执行时按 tier 做决策：
   - core command 失败：停止后续 support/annotation，尝试 deterministic core fallback。
   - support command 失败：跳过该 command，继续后续 support/annotation。
   - annotation/property command 失败：只记录 trace，不影响展示。
3. deterministic core fallback 可以非常保守：
   - 如果 spec 有坐标系和 visible_objects，但 payload 核心失败，则展示 spec-only fallback 卡片。
   - 如果只缺少 support/annotation，则保留已创建 core objects。
   - 如果 `object_count > 0` 且 core expected 大部分存在，展示图并提示“部分辅助元素未显示”。
4. 只有用户显式点击“重新生成可视化”或开发调试开关打开时，才把 runtime trace 放进下一次 Stage 2 prompt。

这样把真实浏览器错误用于**当场保留能运行的部分**，而不是默认消耗新一轮 API 调用。

### 7. Opt F：持久化 validation/runtime 状态

#### 问题

`VisualizationRow` 当前只有 `degraded`，无法表达：

- 后端 static passed，但前端还没真实渲染。
- 前端 runtime passed。
- 前端 runtime partial passed，核心图已显示但部分增强对象失败。
- 前端 runtime failed，已显示 fallback。

#### 设计

短期不加 migration 时，可以先写入 `execution_payload_json.__meta`：

```json
{
  "__meta": {
    "validation_status": "static_passed",
    "runtime_status": "unknown",
    "static_validation_mode": "static",
    "stage2_llm_retry_count": 0,
    "stage2_risk_level": "low",
    "partial_render_allowed": true
  }
}
```

长期可加列：

- `validation_status`
- `runtime_status`
- `runtime_error_kind`
- `runtime_error_message`
- `runtime_partial_failures_json`
- `last_runtime_trace_json`

前端展示逻辑：

- `static_passed + runtime_unknown`: 正在尝试渲染。
- `runtime_passed`: 正常展示。
- `runtime_partial_passed`: 展示已成功的核心图，并提示部分辅助元素未显示。
- `runtime_failed`: 显示 fallback + “重新生成可视化”。
- `spec_only`: 显示规格说明卡片。

### 8. Opt G：前端 sandbox 的小修点

具体代码点：

- `runCommandSteps(...)`：
  - 对每条有 LHS 的 command，执行后检查 `exists(lhs)`。
  - `evalCommandGetLabels` 返回空时不能直接算成功。
  - 按 command tier 判断 core failure 还是 optional failure。
  - trace 增加 `lhs`, `exists_lhs`, `created_labels`, `tier`, `continued_after_failure`。

- `renderNow(...)`：
  - 用 `try/finally` 保证 `setRepaintingActive(true)` 一定恢复。
  - render 成功前统计 `object_count`。
  - `object_count === 0` 时触发 `render.empty_runtime`。
  - metric 中包含 `object_count`, `core_expected_count`, `missing_core_count`, `partial_failure_count`。

- `checkExpectedCreatedObjects(...)`：
  - core missing hard fail。
  - support missing partial pass。
  - soft/annotation missing degraded。
  - 如果 expected list 为空，至少检查 `createdObjects.length > 0`。

这些改动不影响后端数据模型，但能显著减少“什么都没渲染却被当成成功”的情况。

### 9. 建议实施顺序

1. **先改前端 runtime hardening**
   - LHS exists 检查。
   - object_count 空图检查。
   - repainting finally。
   - 新增 trace 字段。

2. **再改 Stage 2 prompt**
   - 增加 2-3 个稳定 few-shot。
   - 增加 `force_static_fallback` 输入开关。
   - system message 中加入“minimum visible object contract”。
   - 明确要求 core/support/annotation 分层，前 5-8 条 command 要能单独成图。

3. **再改后端 draft normalization**
   - 增加 `GeoGebraExecutionPayloadDraft`。
   - commands/expected/optional_script 本地归一。
   - 归一后再进入 strict validator。

4. **最后才考虑手动 runtime trace repair**
   - 默认不自动重试 LLM。
   - 只有用户点击“重新生成可视化”或开发调试时才调用。
   - repair prompt 必须带原 payload 和 fatal trace。
   - repair 后新 row 或覆盖 row，需要产品上先决定。

### 10. 成功标准

完成这轮后，“生成可视化”应满足：

- GeoGebra creation command 静默失败不会被误判为成功。
- 支持对象、注释对象、样式对象失败时，核心图仍能显示。
- 画布没有任何核心对象时才整体 fallback，不再出现空白面板。
- Gemini 小 JSON 结构错误可以本地归一，不直接 spec-only。
- Stage 2 默认更像 few-shot：短 payload、少 styling、core expected objects 明确。
- 高风险 spec 会自动走 static fallback 或备用 spec。
- 默认不因 runtime failure 自动消耗第二次 GeoGebra LLM 生成费用。
- 前端 runtime failure 首先用于部分渲染与降级展示；只在显式手动重生成时作为下一次 prompt 输入。
