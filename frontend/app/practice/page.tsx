'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

type ExamItem = {
  id: string;
  position: number;
  source_question_id: string | null;
  synthesized: boolean;
  statement: string;
  answer_outline: string;
  rubric: string;
};

type ExamDetail = {
  exam_id: string;
  name: string;
  config: Record<string, unknown>;
  created_at: string | null;
  items: ExamItem[];
};

const muted = { color: '#888', fontSize: 12 } as const;
const BASKET_KEY = 'hanswer.practice.basket';

function readBasket(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(BASKET_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeBasket(ids: string[]) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(BASKET_KEY, JSON.stringify(ids));
}

export default function PracticePage() {
  const [basket, setBasket] = useState<string[]>([]);
  const [name, setName] = useState('练习卷');
  const [subject, setSubject] = useState('');
  const [gradeBand, setGradeBand] = useState('');
  const [count, setCount] = useState(5);
  const [distRaw, setDistRaw] = useState('');   // e.g. "1:1,2:2,3:2"
  const [allowSynth, setAllowSynth] = useState(true);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [scores, setScores] = useState<Record<string, 'ok' | 'wrong' | 'unsure'>>({});

  useEffect(() => { setBasket(readBasket()); }, []);

  const difficultyDist = useMemo(() => {
    const out: Record<number, number> = {};
    distRaw.split(/[,\s]+/).filter(Boolean).forEach((p) => {
      const m = p.match(/^(\d+):(\d+)$/);
      if (m) out[Number(m[1])] = Number(m[2]);
    });
    return out;
  }, [distRaw]);

  function removeFromBasket(id: string) {
    const next = basket.filter((x) => x !== id);
    setBasket(next);
    writeBasket(next);
  }

  function clearBasket() {
    setBasket([]);
    writeBasket([]);
  }

  async function generateExam(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setExam(null);
    setRevealed({});
    try {
      const res = await fetch('/api/practice/exam', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          name,
          sources: basket,
          subjects: subject ? [subject] : [],
          grade_bands: gradeBand ? [gradeBand] : [],
          count,
          difficulty_dist: difficultyDist,
          allow_synthesis: allowSynth,
        }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`${res.status}: ${body}`);
      }
      const body = (await res.json()) as ExamDetail;
      setExam(body);
      setScores({});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h1>练习</h1>
      <p style={muted}>
        把题目加入练习篮后生成同类练习卷; 题库不足时 LLM 会合成同模式变体。
      </p>

      <h2 style={{ marginTop: 20 }}>练习篮 ({basket.length})</h2>
      {basket.length === 0 ? (
        <p style={muted}>
          空。在 <Link href="/library">题库</Link> 或 <code>/q/[id]</code> 页点击
          “加入练习篮” 把题目放进来。
        </p>
      ) : (
        <ul style={{ listStyle: 'none', paddingLeft: 0 }}>
          {basket.map((id) => (
            <li key={id} style={{ padding: '4px 0', borderBottom: '1px solid #eee' }}>
              <Link href={`/q/${id}`} style={{ color: '#0366d6' }}>#{id.slice(0, 8)}</Link>
              <button
                type="button"
                onClick={() => removeFromBasket(id)}
                style={{ marginLeft: 8, fontSize: 12 }}
              >移除</button>
            </li>
          ))}
        </ul>
      )}
      {basket.length > 0 && (
        <button type="button" onClick={clearBasket} style={{ marginTop: 4, fontSize: 12 }}>
          清空练习篮
        </button>
      )}

      <h2 style={{ marginTop: 24 }}>生成考卷</h2>
      <form onSubmit={generateExam} style={{ display: 'grid', gap: 8, maxWidth: 520 }}>
        <label>名称
          <input value={name} onChange={(e) => setName(e.target.value)}
            style={{ marginLeft: 8, padding: 4 }} />
        </label>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <label>学科
            <select value={subject} onChange={(e) => setSubject(e.target.value)}
              style={{ marginLeft: 4 }}>
              <option value="">全部</option>
              <option value="math">数学</option>
              <option value="physics">物理</option>
            </select>
          </label>
          <label>学段
            <select value={gradeBand} onChange={(e) => setGradeBand(e.target.value)}
              style={{ marginLeft: 4 }}>
              <option value="">全部</option>
              <option value="junior">初中</option>
              <option value="senior">高中</option>
            </select>
          </label>
          <label>题量
            <input type="number" min={1} max={50} value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              style={{ marginLeft: 4, width: 64 }} />
          </label>
        </div>
        <label>难度分布 (格式 <code>难度:个数</code>, 逗号分隔; 留空则不限)
          <input value={distRaw} onChange={(e) => setDistRaw(e.target.value)}
            placeholder="例如 1:1,2:2,3:2"
            style={{ marginLeft: 8, padding: 4, width: 260 }} />
        </label>
        <label>
          <input type="checkbox" checked={allowSynth}
            onChange={(e) => setAllowSynth(e.target.checked)} />
          题库不足时允许 LLM 合成变体
        </label>
        <button type="submit" disabled={loading} style={{ width: 120 }}>
          {loading ? '生成中…' : '生成考卷'}
        </button>
      </form>

      {error && <p style={{ color: '#c00', marginTop: 12 }}>{error}</p>}

      {exam && (
        <section style={{ marginTop: 24 }}>
          <h2>{exam.name}</h2>
          <p style={muted}>
            共 {exam.items.length} 题 · 考卷 ID <code>{exam.exam_id.slice(0, 8)}</code>
          </p>
          <ScoreSummary scores={scores} total={exam.items.length} />
          <ol style={{ paddingLeft: 24 }}>
            {exam.items.map((it) => {
              const isShown = revealed[it.id];
              return (
                <li key={it.id} style={{ marginBottom: 16 }}>
                  <div>
                    {it.statement || '(题面缺失)'}
                    {it.synthesized && (
                      <span style={{ ...muted, marginLeft: 8 }}>· LLM 合成变体</span>
                    )}
                  </div>
                  {it.source_question_id && (
                    <div style={muted}>
                      来源: <Link href={`/q/${it.source_question_id}`}>
                        #{it.source_question_id.slice(0, 8)}
                      </Link>
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => setRevealed({ ...revealed, [it.id]: !isShown })}
                    style={{ marginTop: 4, fontSize: 12 }}
                  >{isShown ? '收起答案大纲' : '查看答案大纲'}</button>
                  <span style={{ marginLeft: 12, fontSize: 12 }}>
                    自评:
                    {(['ok', 'wrong', 'unsure'] as const).map((s) => (
                      <label key={s} style={{ marginLeft: 8 }}>
                        <input
                          type="radio"
                          name={`score-${it.id}`}
                          checked={scores[it.id] === s}
                          onChange={() => setScores({ ...scores, [it.id]: s })}
                        />
                        {s === 'ok' ? ' 对' : s === 'wrong' ? ' 错' : ' 不确定'}
                      </label>
                    ))}
                  </span>
                  {isShown && (
                    <div style={{
                      marginTop: 4, padding: 8,
                      background: '#f8f8f8', borderLeft: '3px solid #0366d6',
                      whiteSpace: 'pre-wrap', fontSize: 14,
                    }}>
                      <strong>大纲</strong>
                      <div>{it.answer_outline || '(无)'}</div>
                      <strong style={{ display: 'block', marginTop: 8 }}>评分提示</strong>
                      <div>{it.rubric || '(无)'}</div>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      )}
    </section>
  );
}

function ScoreSummary({
  scores, total,
}: { scores: Record<string, 'ok' | 'wrong' | 'unsure'>; total: number }) {
  const counts = { ok: 0, wrong: 0, unsure: 0 };
  for (const v of Object.values(scores)) counts[v] += 1;
  const done = counts.ok + counts.wrong + counts.unsure;
  const pct = total > 0 ? Math.round((counts.ok / total) * 100) : 0;
  return (
    <div style={{
      padding: 10, background: '#f0f4ff', borderRadius: 6,
      marginBottom: 12, display: 'flex', gap: 16, flexWrap: 'wrap',
    }}>
      <span><strong>自评进度:</strong> {done} / {total}</span>
      <span style={{ color: '#0a7c3b' }}>对 {counts.ok}</span>
      <span style={{ color: '#c0392b' }}>错 {counts.wrong}</span>
      <span style={{ color: '#7f5a00' }}>不确定 {counts.unsure}</span>
      <span><strong>正确率:</strong> {pct}%</span>
    </div>
  );
}
