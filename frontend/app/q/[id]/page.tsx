'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { TeX, RichText } from '../../../components/MathText';
import VizSandbox from '../../../components/VizSandbox';
import { apiUrl } from '../../../lib/api';

/**
 * Answer view (§9.2).
 *
 * Consumes SSE from `POST /api/answer/:id` (events ordered per §6).
 * Each section renders the moment it arrives. Math via MathJax (shared MathText);
 * visualizations hosted inside <VizSandbox/> (§3.3).
 */

type SectionName =
  | 'status'
  | 'question_understanding'
  | 'key_points_of_question'
  | 'solution_step'
  | 'visualization'
  | 'key_points_of_answer'
  | 'method_pattern'
  | 'similar_questions'
  | 'knowledge_points'
  | 'self_check'
  | 'sediment'
  | 'error'
  | 'done';

type AnyEv = { name: SectionName; data: any; ts: number };
type PipelineStep = {
  key: string;
  call_index: number;
  label: string;
  description: string;
  state: 'pending' | 'active' | 'done' | 'error' | 'review';
  review_status?: string | null;
  artifact_version?: number;
};
type Pipeline = {
  current_stage: string | null;
  current_call: number;
  total_calls: number;
  completed_calls: number;
  visualizations_generated: boolean;
  error: string | null;
  steps: PipelineStep[];
};
type StageReview = {
  stage: string;
  review_status: 'pending' | 'confirmed' | 'rejected';
  artifact_version: number;
  run_count: number;
  summary: any;
  refs: any;
  review_note: string;
  reviewed_at: string | null;
  updated_at: string | null;
};
type SolutionSummary = {
  solution_id: string;
  ordinal: number;
  title: string;
  is_current: boolean;
  status: string;
  has_answer: boolean;
  visualization_count: number;
  stage_reviews: StageReview[];
};

const h2Style: React.CSSProperties = { marginTop: 24, borderBottom: '1px solid #eee', paddingBottom: 4 };
const mutedStyle: React.CSSProperties = { color: '#888', fontSize: 12 };

export default function QuestionPage({ params: paramsPromise }: { params: Promise<{ id: string }> }) {
  const params = use(paramsPromise);
  const [events, setEvents] = useState<AnyEv[]>([]);
  const [done, setDone] = useState(false);
  const [initial, setInitial] = useState<any | null>(null);
  const [resumeReady, setResumeReady] = useState(false);
  const [running, setRunning] = useState(false);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [stageReviews, setStageReviews] = useState<StageReview[]>([]);
  const [solutions, setSolutions] = useState<SolutionSummary[]>([]);
  const [currentSolutionId, setCurrentSolutionId] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [creatingSolution, setCreatingSolution] = useState(false);
  const [stageActionPending, setStageActionPending] = useState<string | null>(null);
  const [stageNoteDrafts, setStageNoteDrafts] = useState<Record<string, string>>({});

  const withSolution = useCallback((path: string, solutionId?: string | null) => {
    if (!solutionId) return path;
    const sep = path.includes('?') ? '&' : '?';
    return `${path}${sep}solution_id=${encodeURIComponent(solutionId)}`;
  }, []);

  useEffect(() => {
    const target = withSolution(`/api/questions/${params.id}`, currentSolutionId);
    fetch(apiUrl(target)).then((r) => r.json()).then((body) => {
      setInitial(body);
      setSolutions(Array.isArray(body?.solutions) ? body.solutions : []);
      if (typeof body?.current_solution_id === 'string') {
        setCurrentSolutionId((prev) => prev ?? body.current_solution_id);
      }
    }).catch(() => {});
  }, [currentSolutionId, params.id, withSolution]);

  const loadResume = useCallback(async () => {
    const res = await fetch(apiUrl(withSolution(`/api/answer/${params.id}/resume`, currentSolutionId)));
    if (!res.ok) return null;
    const body = await res.json();
    const replay = resumeToEvents(body);
    setEvents(replay);
    setDone(Boolean(body?.complete) || body?.status === 'error');
    setRunning(Boolean(body?.job?.running) || ['solving', 'visualizing', 'indexing'].includes(body?.status));
    setJobStage(typeof body?.job?.stage === 'string' ? body.job.stage : body?.status ?? null);
    setPipeline(body?.pipeline ?? null);
    setStageReviews(Array.isArray(body?.stage_reviews) ? body.stage_reviews : []);
    setSolutions(Array.isArray(body?.solutions) ? body.solutions : []);
    if (typeof body?.current_solution_id === 'string') {
      setCurrentSolutionId((prev) => prev ?? body.current_solution_id);
    }
    return body;
  }, [currentSolutionId, params.id, withSolution]);

  useEffect(() => {
    let cancelled = false;
    loadResume()
      .catch(() => null)
      .finally(() => {
        if (!cancelled) setResumeReady(true);
      });
    return () => { cancelled = true; };
  }, [loadResume]);

  const autoStartedRef = useRef(false);
  useEffect(() => {
    if (!resumeReady || done || autoStartedRef.current) return;
    let cancelled = false;

    (async () => {
      try {
        const first = await loadResume();
        if (cancelled || first?.complete || first?.status === 'error') return;
        if (hasPendingStageReview(first?.stage_reviews)) return;
        if (first?.job?.running || ['solving', 'visualizing', 'indexing'].includes(first?.status)) return;
        autoStartedRef.current = true;
        await fetch(apiUrl(withSolution(`/api/answer/${params.id}/start`, currentSolutionId)), { method: 'POST' });
        if (cancelled) return;
        await loadResume();
      } catch (e) {
        if (!cancelled) {
          setEvents((prev) => [...prev, {
            name: 'error', data: { message: String(e) }, ts: Date.now(),
          }]);
          setDone(true);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [currentSolutionId, done, loadResume, params.id, resumeReady, withSolution]);

  useEffect(() => {
    if (!resumeReady || done || !running) return;
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;
    const poll = () => {
      loadResume().catch(() => {}).finally(() => {
        if (!cancelled) timeoutId = setTimeout(poll, 1500);
      });
    };
    timeoutId = setTimeout(poll, 1500);
    return () => { cancelled = true; clearTimeout(timeoutId); };
  }, [done, loadResume, resumeReady, running]);

  const byName = useMemo(() => groupBy(events), [events]);

  const [inBasket, setInBasket] = useState(false);
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem('hanswer.practice.basket');
      const ids: string[] = raw ? JSON.parse(raw) : [];
      setInBasket(ids.includes(params.id));
    } catch {
      /* noop */
    }
  }, [params.id]);

  function toggleBasket() {
    try {
      const raw = window.localStorage.getItem('hanswer.practice.basket');
      const ids: string[] = raw ? JSON.parse(raw) : [];
      const next = inBasket
        ? ids.filter((x) => x !== params.id)
        : Array.from(new Set([...ids, params.id]));
      window.localStorage.setItem('hanswer.practice.basket', JSON.stringify(next));
      setInBasket(!inBasket);
    } catch {
      /* noop */
    }
  }

  async function restartAnswer() {
    setRestarting(true);
    try {
      await fetch(apiUrl(withSolution(`/api/answer/${params.id}/start`, currentSolutionId)), { method: 'POST' });
      setEvents([]);
      setDone(false);
      setRunning(true);
      setJobStage('queued');
      setPipeline(null);
      await loadResume();
    } finally {
      setRestarting(false);
    }
  }

  async function createNewSolution() {
    setCreatingSolution(true);
    try {
      const res = await fetch(apiUrl(`/api/questions/${params.id}/solutions`), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(await res.text());
      const body = await res.json();
      const sid = typeof body?.solution?.solution_id === 'string' ? body.solution.solution_id : null;
      if (sid) setCurrentSolutionId(sid);
      setEvents([]);
      setDone(false);
      setPipeline(null);
      await loadResume();
    } finally {
      setCreatingSolution(false);
    }
  }

  const pendingReview = useMemo(
    () => getPendingStageReview(stageReviews),
    [stageReviews],
  );

  async function handleStageAction(stage: string, action: 'confirm' | 'rerun') {
    const key = `${stage}:${action}`;
    setStageActionPending(key);
    try {
      const note = stageNoteDrafts[stage] ?? '';
      const res = await fetch(
        apiUrl(
          withSolution(
            action === 'confirm'
              ? `/api/answer/${params.id}/stages/${stage}/confirm`
              : `/api/answer/${params.id}/stages/${stage}/rerun`,
            stage === 'parsed' ? null : currentSolutionId,
          ),
        ),
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ note }),
        },
      );
      if (!res.ok) {
        throw new Error(await res.text());
      }
      await loadResume();
    } catch (e) {
      setEvents((prev) => [...prev, {
        name: 'error',
        data: { message: String(e) },
        ts: Date.now(),
      }]);
    } finally {
      setStageActionPending(null);
    }
  }

  async function handleDirectRerun(stage: string) {
    await handleStageAction(stage, 'rerun');
  }

  return (
    <section className="qpage-grid">
      <aside className="qpage-outline" style={{
        position: 'sticky', top: 12, alignSelf: 'start',
        maxHeight: 'calc(100vh - 80px)', overflowY: 'auto',
        paddingRight: 12, borderRight: '1px solid #eee', fontSize: 13,
      }}>
        <Outline byName={byName} done={done} />
      </aside>

      <article className="qpage-article">
      <h1>题目 #{params.id.slice(0, 8)}</h1>
      {initial && (
        <p style={mutedStyle}>
          {initial.subject} · {initial.grade_band} · 难度 {initial.difficulty} · 状态 {initial.status}
        </p>
      )}
      <button type="button" onClick={toggleBasket} style={{ fontSize: 12, marginBottom: 8 }}>
        {inBasket ? '✓ 已加入练习篮 (点击移除)' : '加入练习篮'}
      </button>
      <div style={{ marginBottom: 12 }}>
        <Link href={`/dialog?questionId=${params.id}`} style={{ fontSize: 13 }}>
          进入多轮追问对话 →
        </Link>
      </div>
      <SolutionSwitcher
        solutions={solutions}
        currentSolutionId={currentSolutionId}
        creating={creatingSolution}
        onSelect={setCurrentSolutionId}
        onCreate={createNewSolution}
      />
      {!done && (
        <p style={mutedStyle}>
          {latestStatus(byName)
            ?? progressHeadline(pipeline)
            ?? statusLabel(jobStage)
            ?? (running ? '解答生成中…' : '正在启动后台解答任务…')}
        </p>
      )}
      <GeminiProgress pipeline={pipeline} done={done} liveMessage={latestStatus(byName)} />
      <StageRerunBoard
        currentSolutionId={currentSolutionId}
        stageReviews={stageReviews}
        noteDrafts={stageNoteDrafts}
        onNoteChange={(stage, value) => setStageNoteDrafts((prev) => ({ ...prev, [stage]: value }))}
        actionPending={stageActionPending}
        onRerun={handleDirectRerun}
      />
      <StageReviewPanel
        review={pendingReview}
        parsed={initial?.parsed || null}
        noteValue={(pendingReview && stageNoteDrafts[pendingReview.stage] !== undefined)
          ? stageNoteDrafts[pendingReview.stage]
          : pendingReview?.review_note || ''}
        onNoteChange={(stage, value) => setStageNoteDrafts((prev) => ({ ...prev, [stage]: value }))}
        actionPending={stageActionPending}
        onConfirm={(stage) => handleStageAction(stage, 'confirm')}
        onRerun={(stage) => handleStageAction(stage, 'rerun')}
      />

      {initial?.parsed && (
        <section style={{ marginBottom: 24 }}>
          <h2 style={h2Style}>题面与原图</h2>
          <div className="result-compare-grid">
            <div className="source-image-card">
              <div className="math-preview-header">
                <span className="preview-badge">原图对照</span>
                <span className="preview-subject-badge">上传原始题面</span>
              </div>
              <img
                src={apiUrl(`/api/ingest/${params.id}/image`)}
                alt="题目原图"
                className="source-image"
              />
            </div>

            <div className="math-preview">
              <div className="math-preview-header">
                <span className="preview-badge">MathJax 题面</span>
                <span className="preview-subject-badge">
                  {initial.subject} · {initial.grade_band} · 难度 {initial.difficulty}
                </span>
              </div>
              <div className="preview-question">
                <RichText text={initial.parsed.question_text || ''} />
              </div>
              {!!(initial.parsed.given || []).length && (
                <div className="preview-section">
                  <span className="preview-label">已知</span>
                  <ul className="preview-list">
                    {initial.parsed.given.map((g: string, i: number) => (
                      <li key={i}><RichText text={g} /></li>
                    ))}
                  </ul>
                </div>
              )}
              {!!(initial.parsed.find || []).length && (
                <div className="preview-section">
                  <span className="preview-label">求</span>
                  <ul className="preview-list">
                    {initial.parsed.find.map((f: string, i: number) => (
                      <li key={i}><RichText text={f} /></li>
                    ))}
                  </ul>
                </div>
              )}
              {initial.parsed.diagram_description && (
                <div className="preview-section">
                  <span className="preview-label">图形描述</span>
                  <div className="math-live-preview math-live-preview-compact">
                    <RichText text={initial.parsed.diagram_description} />
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {byName.question_understanding && (
        <>
          <h2 id="sec-understanding" style={h2Style}>题目理解</h2>
          <Understanding data={byName.question_understanding[0].data} />
        </>
      )}

      {byName.key_points_of_question && (
        <>
          <h2 id="sec-key-q" style={h2Style}>题目关键点</h2>
          <ul>
            {(byName.key_points_of_question[0].data.items || []).map(
              (p: string, i: number) => (<li key={i}><RichText text={p} /></li>),
            )}
          </ul>
        </>
      )}

      {byName.solution_step && (
        <>
          <h2 id="sec-steps" style={h2Style}>分步解答</h2>
          {byName.solution_step
            .slice()
            .sort((a, b) => (a.data.step_index || 0) - (b.data.step_index || 0))
            .map((ev, i) => (
              <article key={i} style={{ marginBottom: 12 }}>
                <h3 style={{ margin: '8px 0' }}>
                  第 {ev.data.step_index} 步 · <RichText text={ev.data.statement} />
                </h3>
                <p><strong>原理:</strong> <RichText text={ev.data.rationale} /></p>
                {ev.data.formula && (
                  <p><TeX src={ev.data.formula} block /></p>
                )}
                <p style={mutedStyle}>
                  为什么这样做: <RichText text={ev.data.why_this_step || ''} />
                </p>
                {ev.data.viz_ref && (
                  <p style={mutedStyle}>关联可视化: <code>{ev.data.viz_ref}</code></p>
                )}
              </article>
            ))}
        </>
      )}

      {byName.key_points_of_answer && (
        <>
          <h2 id="sec-key-a" style={h2Style}>答案关键点</h2>
          <ul>
            {(byName.key_points_of_answer[0].data.items || []).map(
              (p: string, i: number) => (<li key={i}><RichText text={p} /></li>),
            )}
          </ul>
        </>
      )}

      <section id="sec-viz" style={{ marginTop: 28, marginBottom: 12 }}>
        <h2 style={h2Style}>可视化讲解</h2>
        <div style={{ ...mutedStyle, marginBottom: 10 }}>
          把题目的关键步骤放到大图里演示，方便结合上面的解答一起看。
        </div>
        <VizPanel vizEvents={byName.visualization || []} fullWidth />
      </section>

      {byName.method_pattern && (
        <>
          <h2 id="sec-pattern" style={h2Style}>方法模式</h2>
          <Pattern data={byName.method_pattern[0].data} />
        </>
      )}

      {byName.similar_questions && (
        <>
          <h2 id="sec-similar" style={h2Style}>同类题目</h2>
          <SimilarList items={byName.similar_questions[0].data.items} />
        </>
      )}

      {byName.knowledge_points && (
        <>
          <h2 id="sec-kp" style={h2Style}>知识点</h2>
          <ul>
            {(byName.knowledge_points[0].data.items || []).map(
              (kp: any, i: number) => (
                <li key={i}>
                  <code>{kp.node_ref}</code> · 权重 {Number(kp.weight).toFixed(2)}
                </li>
              ),
            )}
          </ul>
        </>
      )}

      {byName.self_check && (
        <>
          <h2 id="sec-check" style={h2Style}>自我检查</h2>
          <ul>
            {(byName.self_check[0].data.items || []).map(
              (p: string, i: number) => (<li key={i}><RichText text={p} /></li>),
            )}
          </ul>
        </>
      )}

      {byName.error && (
        <ErrorPanel events={byName.error} onRetry={restartAnswer} restarting={restarting} />
      )}
      </article>
    </section>
  );
}

// ── helpers ──────────────────────────────────────────────────────

function groupBy(events: AnyEv[]): Partial<Record<SectionName, AnyEv[]>> {
  const out: Partial<Record<SectionName, AnyEv[]>> = {};
  for (const ev of events) {
    (out[ev.name] ??= []).push(ev);
  }
  return out;
}

function latestStatus(byName: Partial<Record<SectionName, AnyEv[]>>): string | null {
  const items = byName.status;
  if (!items?.length) return null;
  const last = items[items.length - 1]?.data;
  return typeof last?.message === 'string' ? last.message : null;
}

function progressHeadline(pipeline: Pipeline | null): string | null {
  if (!pipeline) return null;
  if (pipeline.error) return pipeline.error;
  const active = pipeline.steps.find((step) => step.state === 'active');
  if (active) {
    return `Gemini ${active.call_index}/${pipeline.total_calls} · ${active.label}`;
  }
  const review = pipeline.steps.find((step) => step.state === 'review');
  if (review) {
    return `等待人工确认 · Gemini ${review.call_index}/${pipeline.total_calls} · ${review.label}`;
  }
  if (pipeline.completed_calls >= pipeline.total_calls) {
    return `Gemini ${pipeline.total_calls}/${pipeline.total_calls} · 全部调用完成`;
  }
  return `Gemini ${pipeline.completed_calls}/${pipeline.total_calls} · 等待下一阶段`;
}

function statusLabel(stage: string | null): string | null {
  if (!stage) return null;
  const labels: Record<string, string> = {
    parsed: '题目已解析，等待开始解答。',
    review_parse: '题面解析已完成，等待人工确认。',
    queued: '解答任务已排队。',
    solving: '正在生成教学型答案。',
    review_solve: '解答已生成，等待人工确认。',
    visualizing: '答案已生成，正在补充可视化。',
    review_viz: '可视化已生成，等待人工确认。',
    indexing: '正在写入知识点、方法模式与检索索引。',
    review_index: '索引已生成，等待人工确认。',
    answered: '解答完成。',
    error: '解答失败。',
  };
  return labels[stage] ?? stage;
}

function StatusSteps({ currentStage }: { currentStage: string | null }) {
  const stages = [
    { key: 'solving', label: '生成解答' },
    { key: 'visualizing', label: '生成可视化' },
    { key: 'indexing', label: '写入索引' },
    { key: 'answered', label: '完成' },
  ];
  const active = currentStage ? stages.findIndex((s) => s.key === currentStage) : -1;
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
      {stages.map((stage, idx) => {
        const filled = active >= idx || currentStage === 'done';
        return (
          <div
            key={stage.key}
            style={{
              padding: '4px 10px',
              borderRadius: 999,
              fontSize: 12,
              border: '1px solid #d9d9d9',
              background: filled ? '#eef5ff' : '#fff',
              color: filled ? '#245ea8' : '#777',
            }}
          >
            {filled ? '● ' : '○ '}
            {stage.label}
          </div>
        );
      })}
    </div>
  );
}

function GeminiProgress({ pipeline, done, liveMessage }: { pipeline: Pipeline | null; done: boolean; liveMessage?: string | null }) {
  if (!pipeline) return null;
  return (
    <div style={{
      marginBottom: 18,
      padding: 12,
      border: '1px solid #e6ecf3',
      borderRadius: 8,
      background: '#fafcff',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontWeight: 600 }}>Gemini 调用进度</div>
          <div style={mutedStyle}>
            已完成 {pipeline.completed_calls}/{pipeline.total_calls}
            {done ? ' · 当前任务已完成' : pipeline.current_call ? ` · 当前调用 ${pipeline.current_call}/${pipeline.total_calls}` : ''}
          </div>
        </div>
        <div style={mutedStyle}>
          可视化 {pipeline.visualizations_generated ? '已生成' : '未生成 / 进行中'}
        </div>
      </div>
      <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
        {pipeline.steps.map((step) => {
          const palette =
            step.state === 'done'
              ? { bg: '#eef8f0', fg: '#1f7a3d', border: '#cfe8d5' }
              : step.state === 'active'
                ? { bg: '#eef5ff', fg: '#245ea8', border: '#d5e4fb' }
                : step.state === 'review'
                  ? { bg: '#fff8e8', fg: '#9a6700', border: '#f4d9a4' }
                : step.state === 'error'
                  ? { bg: '#fff1f1', fg: '#b42318', border: '#f4c7c7' }
                  : { bg: '#fff', fg: '#666', border: '#e5e7eb' };
          return (
            <div
              key={step.key}
              style={{
                border: `1px solid ${palette.border}`,
                borderRadius: 8,
                padding: '8px 10px',
                background: palette.bg,
                color: palette.fg,
              }}
            >
              <div style={{ fontWeight: 600 }}>
                {step.state === 'done' ? '✓ ' : step.state === 'active' ? '● ' : step.state === 'review' ? '⌛ ' : step.state === 'error' ? '⚠ ' : '○ '}
                Gemini {step.call_index}/4 · {step.label}
              </div>
              <div style={{ fontSize: 12, marginTop: 2 }}>{step.description}</div>
              {step.state === 'active' && liveMessage && (
                <div style={{ fontSize: 12, marginTop: 4, fontStyle: 'italic' }}>
                  ↳ {liveMessage}
                </div>
              )}
              {step.state === 'review' && (
                <div style={{ fontSize: 12, marginTop: 4 }}>
                  当前版本 v{step.artifact_version || 0}，等待人工确认。
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function hasPendingStageReview(stageReviews: any): boolean {
  return getPendingStageReview(Array.isArray(stageReviews) ? stageReviews : []) !== null;
}

function getPendingStageReview(stageReviews: StageReview[]): StageReview | null {
  const order = ['parsed', 'solving', 'visualizing', 'indexing'];
  for (const stage of order) {
    const row = stageReviews.find((item) => item.stage === stage && item.review_status === 'pending' && item.artifact_version > 0);
    if (row) return row;
  }
  return null;
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    parsed: '解析题面',
    solving: '生成解答',
    visualizing: '生成可视化',
    indexing: '建立索引',
  };
  return labels[stage] ?? stage;
}

function SolutionSwitcher({
  solutions,
  currentSolutionId,
  creating,
  onSelect,
  onCreate,
}: {
  solutions: SolutionSummary[];
  currentSolutionId: string | null;
  creating: boolean;
  onSelect: (solutionId: string) => void;
  onCreate: () => void;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 600 }}>解法版本</div>
        <button className="btn btn-secondary" onClick={onCreate} disabled={creating}>
          {creating ? '创建中…' : '新建解法'}
        </button>
      </div>
      {!!solutions.length && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
          {solutions.map((solution) => {
            const active = solution.solution_id === currentSolutionId;
            return (
              <button
                key={solution.solution_id}
                type="button"
                onClick={() => onSelect(solution.solution_id)}
                style={{
                  border: active ? '1px solid #245ea8' : '1px solid #d9d9d9',
                  background: active ? '#eef5ff' : '#fff',
                  color: active ? '#245ea8' : '#666',
                  borderRadius: 999,
                  padding: '6px 12px',
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                {solution.title}
                {solution.is_current ? ' · 当前' : ''}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StageRerunBoard({
  currentSolutionId,
  stageReviews,
  noteDrafts,
  onNoteChange,
  actionPending,
  onRerun,
}: {
  currentSolutionId: string | null;
  stageReviews: StageReview[];
  noteDrafts: Record<string, string>;
  onNoteChange: (stage: string, value: string) => void;
  actionPending: string | null;
  onRerun: (stage: string) => void;
}) {
  const stages = ['parsed', 'solving', 'visualizing', 'indexing'];
  return (
    <div style={{
      marginBottom: 18,
      padding: 12,
      border: '1px solid #e5e7eb',
      borderRadius: 8,
      background: '#fff',
    }}>
      <div style={{ fontWeight: 700, marginBottom: 10 }}>阶段重跑</div>
      <div style={{ display: 'grid', gap: 10 }}>
        {stages.map((stage) => {
          const review = stageReviews.find((item) => item.stage === stage);
          const disabled = stage !== 'parsed' && !currentSolutionId;
          const pending = actionPending === `${stage}:rerun`;
          return (
            <div key={stage} style={{ border: '1px solid #eee', borderRadius: 8, padding: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <div style={{ fontWeight: 600 }}>{stageLabel(stage)}</div>
                <div style={mutedStyle}>
                  {review ? `v${review.artifact_version} · ${review.review_status}` : '尚未生成'}
                </div>
              </div>
              <textarea
                value={noteDrafts[stage] ?? review?.review_note ?? ''}
                onChange={(e) => onNoteChange(stage, e.target.value)}
                placeholder="补充重跑要求，例如：用更适合初中生理解的方式。"
                style={{
                  width: '100%',
                  minHeight: 68,
                  resize: 'vertical',
                  marginTop: 8,
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: '1px solid #ddd',
                  fontSize: 13,
                  lineHeight: 1.5,
                }}
              />
              <div style={{ marginTop: 8 }}>
                <button className="btn btn-secondary" onClick={() => onRerun(stage)} disabled={disabled || pending}>
                  {pending ? '重跑中…' : `重跑${stageLabel(stage)}`}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function summarizeReview(review: StageReview): string {
  const summary = review.summary || {};
  if (review.stage === 'parsed') {
    return `题干 ${summary.question_text || ''}`;
  }
  if (review.stage === 'solving') {
    return `方法模式 ${summary.method_pattern || '未识别'} · 步骤 ${summary.solution_step_count || 0} · 知识点 ${summary.knowledge_point_count || 0}`;
  }
  if (review.stage === 'visualizing') {
    return `可视化 ${summary.visualization_count || 0} 个`;
  }
  if (review.stage === 'indexing') {
    return `模式 ${summary.pattern_id || '-'} · 知识点 ${summary.kp_count || 0} · 检索单元 ${summary.retrieval_unit_count || 0}`;
  }
  return '';
}

function StageReviewPanel({
  review,
  parsed,
  noteValue,
  onNoteChange,
  actionPending,
  onConfirm,
  onRerun,
}: {
  review: StageReview | null;
  parsed: any | null;
  noteValue: string;
  onNoteChange: (stage: string, value: string) => void;
  actionPending: string | null;
  onConfirm: (stage: string) => void;
  onRerun: (stage: string) => void;
}) {
  if (!review) return null;
  const confirming = actionPending === `${review.stage}:confirm`;
  const rerunning = actionPending === `${review.stage}:rerun`;
  return (
    <div style={{
      marginBottom: 18,
      padding: 12,
      border: '1px solid #f4d9a4',
      borderRadius: 8,
      background: '#fff8e8',
      color: '#8a5b00',
    }}>
      <div style={{ fontWeight: 700 }}>等待人工确认 · {stageLabel(review.stage)}</div>
      <div style={{ fontSize: 13, marginTop: 4 }}>
        当前版本 v{review.artifact_version} · 已运行 {review.run_count} 次
      </div>
      <div style={{ marginTop: 8, lineHeight: 1.6 }}>
        {review.stage === 'parsed' && parsed ? (
          <StageParsedReview parsed={parsed} />
        ) : (
          summarizeReview(review)
        )}
      </div>
      <div style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>补充要求</div>
        <textarea
          value={noteValue}
          onChange={(e) => onNoteChange(review.stage, e.target.value)}
          placeholder={
            review.stage === 'parsed'
              ? '例如：这是面向初中生的题目，请按初中生能理解的方式解析题面。'
              : review.stage === 'solving'
                ? '例如：这是面向初中生的题目，用初中知识和更清晰的分步讲解回答。'
                : '例如：请减少装饰性内容，优先突出关键教学信息。'
          }
          style={{
            width: '100%',
            minHeight: 88,
            resize: 'vertical',
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid #e7c97b',
            background: '#fffdf7',
            color: '#5f4200',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        />
        <div style={{ marginTop: 6, fontSize: 12, color: '#7a5c1d' }}>
          确认时: 作为下一阶段的生成要求。驳回并重跑时: 作为本阶段重跑要求。
        </div>
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn btn-secondary" onClick={() => onConfirm(review.stage)} disabled={confirming || rerunning}>
          {confirming ? '确认中…' : '确认并进入下一阶段'}
        </button>
        <button className="btn btn-secondary" onClick={() => onRerun(review.stage)} disabled={confirming || rerunning}>
          {rerunning ? '重跑中…' : '驳回并重跑本阶段'}
        </button>
      </div>
    </div>
  );
}

function StageParsedReview({ parsed }: { parsed: any }) {
  return (
    <div style={{
      display: 'grid',
      gap: 10,
      padding: 10,
      borderRadius: 8,
      background: 'rgba(255,255,255,0.55)',
      color: '#5f4200',
    }}>
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>题目</div>
        <div>
          <RichText text={parsed.question_text || ''} />
        </div>
      </div>
      {!!(parsed.given || []).length && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>已知</div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {parsed.given.map((item: string, idx: number) => (
              <li key={idx}><RichText text={item} /></li>
            ))}
          </ul>
        </div>
      )}
      {!!(parsed.find || []).length && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>求</div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {parsed.find.map((item: string, idx: number) => (
              <li key={idx}><RichText text={item} /></li>
            ))}
          </ul>
        </div>
      )}
      {parsed.diagram_description && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>图形描述</div>
          <div>
            <RichText text={parsed.diagram_description} />
          </div>
        </div>
      )}
      <div style={{ fontSize: 12, color: '#7a5c1d' }}>
        学科 {parsed.subject || '-'} · 学段 {parsed.grade_band || '-'} · 难度 {parsed.difficulty ?? '-'}
      </div>
    </div>
  );
}

function resumeToEvents(body: any): AnyEv[] {
  const now = Date.now();
  const sections = Array.isArray(body?.sections) ? body.sections : [];
  const visualizations = Array.isArray(body?.visualizations) ? body.visualizations : [];
  return [
    ...sections.map((sec: any, idx: number) => ({
      name: sec.section as SectionName,
      data: sec.payload,
      ts: now + idx,
    })),
    ...visualizations.map((viz: any, idx: number) => ({
      name: 'visualization' as const,
      data: viz,
      ts: now + sections.length + idx,
    })),
  ];
}

function Understanding({ data }: { data: any }) {
  return (
    <div>
      <p><strong>重述:</strong> <RichText text={data.restated_question || ''} /></p>
      {!!(data.givens || []).length && (
        <>
          <p><strong>已知:</strong></p>
          <ul>{data.givens.map((g: string, i: number) => (<li key={i}><RichText text={g} /></li>))}</ul>
        </>
      )}
      {!!(data.unknowns || []).length && (
        <>
          <p><strong>求:</strong></p>
          <ul>{data.unknowns.map((g: string, i: number) => (<li key={i}><RichText text={g} /></li>))}</ul>
        </>
      )}
      {!!(data.implicit_conditions || []).length && (
        <>
          <p><strong>隐含条件:</strong></p>
          <ul>{data.implicit_conditions.map((g: string, i: number) => (<li key={i}>{g}</li>))}</ul>
        </>
      )}
    </div>
  );
}

function ErrorPanel({
  events,
  onRetry,
  restarting,
}: {
  events: AnyEv[];
  onRetry: () => void;
  restarting: boolean;
}) {
  const latest = events[events.length - 1]?.data || {};
  const message = typeof latest?.message === 'string' ? latest.message : '解答失败。';
  const hint = typeof latest?.hint === 'string' ? latest.hint : null;
  const raw = typeof latest?.raw_message === 'string' ? latest.raw_message : null;
  const failedStage = typeof latest?.failed_stage === 'string' ? latest.failed_stage : null;
  const isTimeout = latest?.kind === 'timeout';
  const isServiceOverloaded = latest?.kind === 'service_overloaded';

  return (
    <div style={{
      marginTop: 24,
      background: '#fff1f1',
      border: '1px solid #f4c7c7',
      borderRadius: 10,
      padding: 14,
      color: '#8f1d1d',
    }}>
      <div style={{ fontWeight: 700, marginBottom: 8 }}>
        {isTimeout ? 'Gemini 调用超时' : isServiceOverloaded ? 'Gemini 服务繁忙' : '解答失败'}
      </div>
      <div style={{ lineHeight: 1.6 }}>{message}</div>
      {failedStage && (
        <div style={{ marginTop: 8, fontSize: 13 }}>
          失败阶段: <code>{failedStage}</code>
        </div>
      )}
      {hint && (
        <div style={{ marginTop: 8, fontSize: 13 }}>{hint}</div>
      )}
      {raw && raw !== message && (
        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer' }}>查看原始错误</summary>
          <div style={{ marginTop: 8, fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-word' }}>
            {raw}
          </div>
        </details>
      )}
      <div style={{ marginTop: 12 }}>
        <button className="btn btn-secondary" onClick={onRetry} disabled={restarting}>
          {restarting ? '重新启动中…' : '重新开始解答'}
        </button>
      </div>
    </div>
  );
}

function Pattern({ data }: { data: any }) {
  return (
    <div>
      <p><strong><RichText text={data.name_cn || ''} /></strong></p>
      <p style={mutedStyle}><RichText text={data.when_to_use || ''} /></p>
      <ol>
        {(data.general_procedure || []).map((p: string, i: number) => (
          <li key={i}><RichText text={p} /></li>
        ))}
      </ol>
      {!!(data.pitfalls || []).length && (
        <>
          <p style={{ marginTop: 8 }}><strong>常见陷阱:</strong></p>
          <ul>
            {data.pitfalls.map((p: string, i: number) => (
              <li key={i}><RichText text={p} /></li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function SimilarList({ items }: { items: any[] }) {
  return (
    <ol>
      {(items || []).map((s, i) => (
        <li key={i} style={{ marginBottom: 8 }}>
          <div><RichText text={s.statement || ''} /></div>
          <div style={mutedStyle}>
            难度变化 {s.difficulty_delta >= 0 ? `+${s.difficulty_delta}` : s.difficulty_delta} ·
            答题大纲: <RichText text={s.answer_outline || ''} />
          </div>
        </li>
      ))}
    </ol>
  );
}


// ── Left rail: streaming section outline with completion markers ──

const OUTLINE_SPEC: { id: string; label: string; evName: SectionName }[] = [
  { id: 'sec-understanding', label: '题目理解', evName: 'question_understanding' },
  { id: 'sec-key-q',         label: '题目关键点', evName: 'key_points_of_question' },
  { id: 'sec-steps',         label: '分步解答', evName: 'solution_step' },
  { id: 'sec-key-a',         label: '答案关键点', evName: 'key_points_of_answer' },
  { id: 'sec-viz',           label: '可视化讲解', evName: 'visualization' },
  { id: 'sec-pattern',       label: '方法模式', evName: 'method_pattern' },
  { id: 'sec-similar',       label: '同类题目', evName: 'similar_questions' },
  { id: 'sec-kp',            label: '知识点', evName: 'knowledge_points' },
  { id: 'sec-check',         label: '自我检查', evName: 'self_check' },
];

function Outline({
  byName, done,
}: { byName: Partial<Record<SectionName, AnyEv[]>>; done: boolean }) {
  const filled = OUTLINE_SPEC.filter((s) => {
    if (s.evName === 'visualization') return true;
    return !!byName[s.evName]?.length;
  }).length;
  return (
    <nav>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>
        大纲 · {filled}/{OUTLINE_SPEC.length}{done ? ' ✓' : ''}
      </div>
      <ul style={{ listStyle: 'none', paddingLeft: 0, margin: 0 }}>
        {OUTLINE_SPEC.map((s) => {
          const filledNow = s.evName === 'visualization' ? true : !!byName[s.evName]?.length;
          return (
            <li key={s.id} style={{ margin: '4px 0' }}>
              <a
                href={`#${s.id}`}
                style={{
                  color: filledNow ? '#0366d6' : '#999',
                  textDecoration: 'none',
                }}
              >
                <span style={{ marginRight: 6 }}>{filledNow ? '●' : '○'}</span>
                {s.label}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}


// ── Visualization section ─────────────────────────────────────────

function VizPanel({
  vizEvents,
  fullWidth = false,
}: {
  vizEvents: AnyEv[];
  fullWidth?: boolean;
}) {
  const [activeIdx, setActiveIdx] = useState(0);
  if (vizEvents.length === 0) {
    return (
      <div style={{
        padding: 12, border: '1px dashed #ddd', borderRadius: 6,
        color: '#999', fontSize: 13,
      }}>
        暂无可视化。生成中…
      </div>
    );
  }
  const active = vizEvents[Math.min(activeIdx, vizEvents.length - 1)];
  return (
    <div style={{
      border: '1px solid #e5e7eb',
      borderRadius: 12,
      background: '#fff',
      padding: fullWidth ? 16 : 8,
      boxShadow: fullWidth ? '0 1px 3px rgba(0,0,0,0.04)' : 'none',
    }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {vizEvents.map((ev, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setActiveIdx(i)}
            style={{
              padding: '6px 12px', fontSize: 12,
              border: '1px solid #ddd', borderRadius: 4,
              background: i === activeIdx ? '#eef5ff' : '#fff',
              cursor: 'pointer',
            }}
            title={ev.data.title_cn}
          >
            {i + 1}. {(ev.data.title_cn || '').slice(0, 12)}
          </button>
        ))}
      </div>
      <div style={{ padding: fullWidth ? 12 : 8, border: '1px solid #eee', borderRadius: 10 }}>
        <div style={{ fontWeight: 700, marginBottom: 6, fontSize: fullWidth ? 18 : 16 }}>{active.data.title_cn}</div>
        <div style={{ ...mutedStyle, marginBottom: 10, fontSize: 13 }}>
          学习目标: <RichText text={active.data.learning_goal || ''} />
        </div>
        <VizSandbox
          key={active.data.id}
          vizId={active.data.id}
          engine={active.data.engine}
          jsxCode={active.data.jsx_code}
          ggbCommands={active.data.ggb_commands}
          ggbSettings={active.data.ggb_settings}
          params={active.data.params}
          height={fullWidth ? 560 : 420}
        />
        {(active.data.interactive_hints || []).length > 0 && (
          <ul style={{ ...mutedStyle, marginTop: 10, fontSize: 13 }}>
            {active.data.interactive_hints.map((h: string, j: number) => (
              <li key={j}><RichText text={h} /></li>
            ))}
          </ul>
        )}
        <div style={{ ...mutedStyle, marginTop: 10, fontSize: 13 }}>
          <RichText text={active.data.caption_cn || ''} />
        </div>
      </div>
    </div>
  );
}
