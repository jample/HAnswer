'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { RichText } from '../../components/MathText';
import { apiUrl } from '../../lib/api';

type QuestionRow = {
  question_id: string;
  subject: string;
  grade_band: string;
  difficulty: number;
  status: string;
  question_text: string;
  pattern_name: string | null;
  seen_count: number;
  created_at: string | null;
};

type SimilarHit = {
  question_id: string;
  score: number;
  cosine: number;
  pattern_match: number;
  kp_overlap: number;
  rrf_score?: number | null;
  route_ranks?: Record<string, number> | null;
  matched_unit_kinds?: string[] | null;
  matched_unit_titles?: string[] | null;
  subject: string;
  grade_band: string;
  difficulty: number;
  question_text: string;
  pattern_name: string | null;
  shared_kp_names: string[] | null;
};

const muted = { color: '#888', fontSize: 12 } as const;

export default function LibraryPage() {
  const [items, setItems] = useState<QuestionRow[]>([]);
  const [subject, setSubject] = useState('');
  const [gradeBand, setGradeBand] = useState('');
  const [difficulty, setDifficulty] = useState('');
  const [textQ, setTextQ] = useState('');
  const [searchMode, setSearchMode] = useState<'list' | 'text'>('list');
  const [textResults, setTextResults] = useState<SimilarHit[]>([]);
  const [strategy, setStrategy] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (subject) p.set('subject', subject);
    if (gradeBand) p.set('grade_band', gradeBand);
    if (difficulty) {
      p.set('difficulty_min', difficulty);
      p.set('difficulty_max', difficulty);
    }
    return p.toString();
  }, [subject, gradeBand, difficulty]);

  useEffect(() => {
    if (searchMode !== 'list') return;
    setLoading(true);
    fetch(apiUrl(`/api/questions${qs ? '?' + qs : ''}`))
      .then((r) => r.json())
      .then((d) => setItems(d.items || []))
      .finally(() => setLoading(false));
  }, [qs, searchMode]);

  async function runTextSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!textQ.trim()) return;
    setLoading(true);
    setSearchMode('text');
    try {
      const res = await fetch(apiUrl('/api/retrieve/similar'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          mode: 'text',
          query: textQ,
          filters: {
            subject: subject || null,
            grade_band: gradeBand || null,
          },
          k: 20,
        }),
      });
      const d = await res.json();
      setTextResults(d.hits || []);
      setStrategy(d.strategy || null);
    } finally {
      setLoading(false);
    }
  }

  function resetSearch() {
    setSearchMode('list');
    setTextQ('');
    setTextResults([]);
    setStrategy(null);
  }

  return (
    <section className="page-section">
      <h1>题库</h1>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <label>学科
          <select value={subject} onChange={(e) => setSubject(e.target.value)} style={{ marginLeft: 4 }}>
            <option value="">全部</option>
            <option value="math">数学</option>
            <option value="physics">物理</option>
          </select>
        </label>
        <label>学段
          <select value={gradeBand} onChange={(e) => setGradeBand(e.target.value)} style={{ marginLeft: 4 }}>
            <option value="">全部</option>
            <option value="junior">初中</option>
            <option value="senior">高中</option>
          </select>
        </label>
        <label>难度
          <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)} style={{ marginLeft: 4 }}>
            <option value="">全部</option>
            {[1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
      </div>

      <form onSubmit={runTextSearch} style={{ marginTop: 12, display: 'flex', gap: 8 }}>
        <input
          type="text"
          placeholder="文本 / 方法 / 关键点 / 扩展思路检索"
          value={textQ}
          onChange={(e) => setTextQ(e.target.value)}
          style={{ flex: 1, padding: '6px 8px', border: '1px solid #ccc', borderRadius: 4 }}
        />
        <button type="submit" disabled={loading}>检索</button>
        {searchMode === 'text' && (
          <button type="button" onClick={resetSearch}>清除</button>
        )}
      </form>

      {loading && <p style={muted}>加载中…</p>}

      {searchMode === 'list' && (
        <ul style={{ listStyle: 'none', paddingLeft: 0, marginTop: 16 }}>
          {items.map((it) => (
            <li key={it.question_id} style={rowStyle}>
              <Link href={`/q/${it.question_id}`} style={{ color: '#0366d6', textDecoration: 'none' }}>
                <strong>#{it.question_id.slice(0, 8)}</strong>
                {' · '}
                <div className="q-text-preview">
                  <RichText text={it.question_text || '(无文本)'} />
                </div>
              </Link>
              <div style={muted}>
                {it.subject} · {it.grade_band} · 难度 {it.difficulty}
                {it.pattern_name && <> · 模式 <code>{it.pattern_name}</code></>}
                {it.seen_count > 1 && <> · 出现 {it.seen_count} 次</>}
              </div>
            </li>
          ))}
          {items.length === 0 && !loading && (
            <li style={muted}>暂无题目,上传第一道题目开始积累吧。</li>
          )}
        </ul>
      )}

      {searchMode === 'text' && (
        <div style={{ marginTop: 16 }}>
          {strategy && (
            <p style={muted}>
              检索策略: <code>{strategy}</code>
            </p>
          )}
        <ol style={{ paddingLeft: 20, marginTop: 16 }}>
          {textResults.map((h) => (
            <li key={h.question_id} style={{ marginBottom: 8 }}>
              <Link href={`/q/${h.question_id}`} style={{ color: '#0366d6', textDecoration: 'none' }}>
                <div className="q-text-preview">
                  <RichText text={h.question_text || '(无文本)'} />
                </div>
              </Link>
              <div style={muted}>
                得分 {h.score.toFixed(2)} (cos {h.cosine.toFixed(2)}
                {h.pattern_match ? ' · 同模式' : ''}
                {h.kp_overlap > 0 ? ` · kp重合 ${h.kp_overlap.toFixed(2)}` : ''})
                {h.pattern_name && <> · <code>{h.pattern_name}</code></>}
              </div>
              {h.route_ranks && Object.keys(h.route_ranks).length > 0 && (
                <div style={muted}>
                  路由排名:
                  {' '}
                  {Object.entries(h.route_ranks)
                    .map(([name, rank]) => `${name}#${rank}`)
                    .join(' · ')}
                  {typeof h.rrf_score === 'number' && ` · RRF ${h.rrf_score.toFixed(3)}`}
                </div>
              )}
              {h.matched_unit_kinds && h.matched_unit_kinds.length > 0 && (
                <div style={muted}>
                  命中语义单元:
                  {' '}
                  {h.matched_unit_kinds.join(' · ')}
                  {h.matched_unit_titles && h.matched_unit_titles.length > 0
                    ? ` · ${h.matched_unit_titles.join(' / ')}`
                    : ''}
                </div>
              )}
            </li>
          ))}
          {textResults.length === 0 && !loading && (
            <li style={muted}>无匹配结果。</li>
          )}
        </ol>
        </div>
      )}
    </section>
  );
}

const rowStyle: React.CSSProperties = {
  padding: '8px 0',
  borderBottom: '1px solid #eee',
};
