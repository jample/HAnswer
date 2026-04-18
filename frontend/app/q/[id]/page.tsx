'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useMemo, useState } from 'react';

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
  state: 'pending' | 'active' | 'done' | 'error';
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
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    fetch(apiUrl(`/api/questions/${params.id}`)).then((r) => r.json()).then(setInitial).catch(() => {});
  }, [params.id]);

  const loadResume = useCallback(async () => {
    const res = await fetch(apiUrl(`/api/answer/${params.id}/resume`));
    if (!res.ok) return null;
    const body = await res.json();
    const replay = resumeToEvents(body);
    setEvents(replay);
    setDone(Boolean(body?.complete) || body?.status === 'error');
    setRunning(Boolean(body?.job?.running) || ['solving', 'visualizing', 'indexing'].includes(body?.status));
    setJobStage(typeof body?.job?.stage === 'string' ? body.job.stage : body?.status ?? null);
    setPipeline(body?.pipeline ?? null);
    return body;
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    loadResume()
      .catch(() => null)
      .finally(() => {
        if (!cancelled) setResumeReady(true);
      });
    return () => { cancelled = true; };
  }, [loadResume]);

  useEffect(() => {
    if (!resumeReady || done) return;
    let cancelled = false;
    let intervalId: number | undefined;

    (async () => {
      try {
        await fetch(apiUrl(`/api/answer/${params.id}/start`), { method: 'POST' });
        if (cancelled) return;
        const first = await loadResume();
        if (cancelled || first?.complete || first?.status === 'error') return;
        intervalId = window.setInterval(() => {
          loadResume().catch(() => {});
        }, 1500);
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
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
  }, [done, loadResume, params.id, resumeReady]);

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
      await fetch(apiUrl(`/api/answer/${params.id}/start`), { method: 'POST' });
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
      {!done && (
        <p style={mutedStyle}>
          {latestStatus(byName)
            ?? progressHeadline(pipeline)
            ?? statusLabel(jobStage)
            ?? (running ? '解答生成中…' : '正在启动后台解答任务…')}
        </p>
      )}
      <GeminiProgress pipeline={pipeline} done={done} />

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
                  第 {ev.data.step_index} 步 · {ev.data.statement}
                </h3>
                <p><strong>原理:</strong> <RichText text={ev.data.rationale} /></p>
                {ev.data.formula && (
                  <p><TeX src={ev.data.formula} block /></p>
                )}
                <p style={mutedStyle}>为什么这样做: {ev.data.why_this_step}</p>
                {ev.data.viz_ref && (
                  <p style={mutedStyle}>关联可视化: <code>{ev.data.viz_ref}</code></p>
                )}
              </article>
            ))}
        </>
      )}

      {byName.visualization && (
        <p style={mutedStyle}>
          可视化: {byName.visualization.length} 个 (见右侧面板) →
        </p>
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
              (p: string, i: number) => (<li key={i}>{p}</li>),
            )}
          </ul>
        </>
      )}

      {byName.error && (
        <ErrorPanel events={byName.error} onRetry={restartAnswer} restarting={restarting} />
      )}
      </article>

      <aside className="qpage-viz" style={{
        position: 'sticky', top: 12, alignSelf: 'start',
        maxHeight: 'calc(100vh - 80px)', overflowY: 'auto',
      }}>
        <VizPanel vizEvents={byName.visualization || []} />
      </aside>
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
  if (pipeline.completed_calls >= pipeline.total_calls) {
    return `Gemini ${pipeline.total_calls}/${pipeline.total_calls} · 全部调用完成`;
  }
  return `Gemini ${pipeline.completed_calls}/${pipeline.total_calls} · 等待下一阶段`;
}

function statusLabel(stage: string | null): string | null {
  if (!stage) return null;
  const labels: Record<string, string> = {
    parsed: '题目已解析，等待开始解答。',
    queued: '解答任务已排队。',
    solving: '正在生成教学型答案。',
    visualizing: '答案已生成，正在补充可视化。',
    indexing: '正在写入知识点、方法模式与检索索引。',
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

function GeminiProgress({ pipeline, done }: { pipeline: Pipeline | null; done: boolean }) {
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
                {step.state === 'done' ? '✓ ' : step.state === 'active' ? '● ' : step.state === 'error' ? '⚠ ' : '○ '}
                Gemini {step.call_index}/4 · {step.label}
              </div>
              <div style={{ fontSize: 12, marginTop: 2 }}>{step.description}</div>
            </div>
          );
        })}
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
        {isTimeout ? 'Gemini 调用超时' : '解答失败'}
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
      <p><strong>{data.name_cn}</strong></p>
      <p style={mutedStyle}>{data.when_to_use}</p>
      <ol>
        {(data.general_procedure || []).map((p: string, i: number) => (<li key={i}>{p}</li>))}
      </ol>
      {!!(data.pitfalls || []).length && (
        <>
          <p style={{ marginTop: 8 }}><strong>常见陷阱:</strong></p>
          <ul>
            {data.pitfalls.map((p: string, i: number) => (<li key={i}>{p}</li>))}
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
            答题大纲: {s.answer_outline}
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
  { id: 'sec-pattern',       label: '方法模式', evName: 'method_pattern' },
  { id: 'sec-similar',       label: '同类题目', evName: 'similar_questions' },
  { id: 'sec-kp',            label: '知识点', evName: 'knowledge_points' },
  { id: 'sec-check',         label: '自我检查', evName: 'self_check' },
];

function Outline({
  byName, done,
}: { byName: Partial<Record<SectionName, AnyEv[]>>; done: boolean }) {
  const filled = OUTLINE_SPEC.filter((s) => byName[s.evName]?.length).length;
  return (
    <nav>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>
        大纲 · {filled}/{OUTLINE_SPEC.length}{done ? ' ✓' : ''}
      </div>
      <ul style={{ listStyle: 'none', paddingLeft: 0, margin: 0 }}>
        {OUTLINE_SPEC.map((s) => {
          const filledNow = !!byName[s.evName]?.length;
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


// ── Right rail: sticky viz panel with tabs per visualization ──────

function VizPanel({ vizEvents }: { vizEvents: AnyEv[] }) {
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
    <div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
        {vizEvents.map((ev, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setActiveIdx(i)}
            style={{
              padding: '4px 10px', fontSize: 12,
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
      <div style={{ padding: 8, border: '1px solid #eee', borderRadius: 6 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{active.data.title_cn}</div>
        <div style={{ ...mutedStyle, marginBottom: 6 }}>
          学习目标: {active.data.learning_goal}
        </div>
        <VizSandbox
          key={active.data.id}
          vizId={active.data.id}
          jsxCode={active.data.jsx_code}
          params={active.data.params}
        />
        {(active.data.interactive_hints || []).length > 0 && (
          <ul style={{ ...mutedStyle, marginTop: 6 }}>
            {active.data.interactive_hints.map((h: string, j: number) => (
              <li key={j}>{h}</li>
            ))}
          </ul>
        )}
        <div style={{ ...mutedStyle, marginTop: 6 }}>{active.data.caption_cn}</div>
      </div>
    </div>
  );
}
