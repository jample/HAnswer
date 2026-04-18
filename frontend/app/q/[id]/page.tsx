'use client';

import 'katex/dist/katex.min.css';

import { useEffect, useMemo, useRef, useState } from 'react';
import katex from 'katex';

import VizSandbox from '../../../components/VizSandbox';

/**
 * Answer view (§9.2).
 *
 * Consumes SSE from `POST /api/answer/:id` (events ordered per §6).
 * Each section renders the moment it arrives. LaTeX via KaTeX;
 * visualizations hosted inside <VizSandbox/> (§3.3).
 */

type SectionName =
  | 'question_understanding'
  | 'key_points_of_question'
  | 'solution_step'
  | 'visualization'
  | 'key_points_of_answer'
  | 'method_pattern'
  | 'similar_questions'
  | 'knowledge_points'
  | 'self_check'
  | 'error'
  | 'done';

type AnyEv = { name: SectionName; data: any; ts: number };

function TeX({ src, block = false }: { src: string; block?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    try {
      katex.render(src, ref.current, { displayMode: block, throwOnError: false });
    } catch {
      ref.current.textContent = src;
    }
  }, [src, block]);
  return <span ref={ref} />;
}

function RichText({ text }: { text: string }) {
  const parts = text.split(/(\$[^$]+\$)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('$') && p.endsWith('$') && p.length >= 2 ? (
          <TeX key={i} src={p.slice(1, -1)} />
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

const h2Style: React.CSSProperties = { marginTop: 24, borderBottom: '1px solid #eee', paddingBottom: 4 };
const mutedStyle: React.CSSProperties = { color: '#888', fontSize: 12 };

export default function QuestionPage({ params }: { params: { id: string } }) {
  const [events, setEvents] = useState<AnyEv[]>([]);
  const [connected, setConnected] = useState(false);
  const [done, setDone] = useState(false);
  const [initial, setInitial] = useState<any | null>(null);

  useEffect(() => {
    fetch(`/api/questions/${params.id}`).then((r) => r.json()).then(setInitial).catch(() => {});
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();

    (async () => {
      try {
        const res = await fetch(`/api/answer/${params.id}`, {
          method: 'POST',
          signal: ctrl.signal,
          headers: { accept: 'text/event-stream' },
        });
        if (!res.body) return;
        setConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (!cancelled) {
          const { value, done: rdone } = await reader.read();
          if (rdone) break;
          buf += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const raw = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const ev = parseSSE(raw);
            if (!ev) continue;
            setEvents((prev) => [...prev, { ...ev, ts: Date.now() }]);
            if (ev.name === 'done' || ev.name === 'error') setDone(true);
          }
        }
      } catch (e) {
        if (!cancelled) setEvents((prev) => [...prev, {
          name: 'error', data: { message: String(e) }, ts: Date.now(),
        }]);
      } finally {
        setConnected(false);
      }
    })();

    return () => { cancelled = true; ctrl.abort(); };
  }, [params.id]);

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

  return (
    <section style={{
      display: 'grid', gap: 16,
      gridTemplateColumns: 'minmax(180px, 220px) minmax(360px, 1fr) minmax(280px, 380px)',
      alignItems: 'start',
    }}
    className="qpage-grid"
    >
      <aside style={{
        position: 'sticky', top: 12, alignSelf: 'start',
        maxHeight: 'calc(100vh - 40px)', overflowY: 'auto',
        paddingRight: 8, borderRight: '1px solid #eee', fontSize: 13,
      }}>
        <Outline byName={byName} done={done} />
      </aside>

      <article>
      <h1>题目 #{params.id.slice(0, 8)}</h1>
      {initial && (
        <p style={mutedStyle}>
          {initial.subject} · {initial.grade_band} · 难度 {initial.difficulty} · 状态 {initial.status}
        </p>
      )}
      <button type="button" onClick={toggleBasket} style={{ fontSize: 12, marginBottom: 8 }}>
        {inBasket ? '✓ 已加入练习篮 (点击移除)' : '加入练习篮'}
      </button>
      {!done && connected && <p style={mutedStyle}>解答生成中…</p>}

      {initial?.parsed && (
        <details>
          <summary>解析题目</summary>
          <pre style={{ background: '#f6f6f6', padding: 8 }}>
            {JSON.stringify(initial.parsed, null, 2)}
          </pre>
        </details>
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
        <div style={{ marginTop: 24, background: '#ffecec', padding: 12, color: '#b00020' }}>
          {byName.error.map((ev, i) => (
            <div key={i}>⚠️ {JSON.stringify(ev.data)}</div>
          ))}
        </div>
      )}
      </article>

      <aside style={{
        position: 'sticky', top: 12, alignSelf: 'start',
        maxHeight: 'calc(100vh - 40px)', overflowY: 'auto',
      }}>
        <VizPanel vizEvents={byName.visualization || []} />
      </aside>
    </section>
  );
}

// ── helpers ──────────────────────────────────────────────────────

function parseSSE(raw: string): { name: SectionName; data: any } | null {
  let event = 'message';
  let data = '';
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!data) return null;
  try {
    return { name: event as SectionName, data: JSON.parse(data) };
  } catch {
    return { name: event as SectionName, data: { raw: data } };
  }
}

function groupBy(events: AnyEv[]): Partial<Record<SectionName, AnyEv[]>> {
  const out: Partial<Record<SectionName, AnyEv[]>> = {};
  for (const ev of events) {
    (out[ev.name] ??= []).push(ev);
  }
  return out;
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

