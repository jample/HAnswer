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
  learning_ready: boolean;
  confirmed_stage_count: number;
  review_statuses: Record<string, string>;
  topic_path: string[];
  method_labels: string[];
  target_types: string[];
  novelty_flags: string[];
  textbook_stage: string;
};
type Facets = {
  methods: string[];
  topics: string[];
  target_types: string[];
  novelty_flags: string[];
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
  solution_id?: string | null;
};

const muted = { color: '#888', fontSize: 12 } as const;

export default function LibraryPage() {
  const [items, setItems] = useState<QuestionRow[]>([]);
  const [subject, setSubject] = useState('');
  const [gradeBand, setGradeBand] = useState('');
  const [difficulty, setDifficulty] = useState('');
  const [textQ, setTextQ] = useState('');
  const [topic, setTopic] = useState('');
  const [method, setMethod] = useState('');
  const [targetType, setTargetType] = useState('');
  const [noveltyFlag, setNoveltyFlag] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [sort, setSort] = useState<'recommended' | 'recent' | 'popular'>('recommended');
  const [learningReadyOnly, setLearningReadyOnly] = useState(true);
  const [searchMode, setSearchMode] = useState<'list' | 'text'>('list');
  const [textResults, setTextResults] = useState<SimilarHit[]>([]);
  const [strategy, setStrategy] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [facets, setFacets] = useState<Facets>({ methods: [], topics: [], target_types: [], novelty_flags: [] });

  function purgeQuestionLocalRefs(questionId: string) {
    try {
      const rawRecent = window.localStorage.getItem('hanswer.recent_uploads');
      if (rawRecent) {
        const parsed = JSON.parse(rawRecent);
        if (Array.isArray(parsed)) {
          window.localStorage.setItem(
            'hanswer.recent_uploads',
            JSON.stringify(parsed.filter((item: { id?: string }) => item?.id !== questionId)),
          );
        }
      }
      const rawBasket = window.localStorage.getItem('hanswer.practice.basket');
      if (rawBasket) {
        const parsed = JSON.parse(rawBasket);
        if (Array.isArray(parsed)) {
          window.localStorage.setItem(
            'hanswer.practice.basket',
            JSON.stringify(parsed.filter((item: string) => item !== questionId)),
          );
        }
      }
    } catch {
      /* noop */
    }
  }

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (subject) p.set('subject', subject);
    if (gradeBand) p.set('grade_band', gradeBand);
    if (difficulty) {
      p.set('difficulty_min', difficulty);
      p.set('difficulty_max', difficulty);
    }
    if (topic) p.set('topic', topic);
    if (method) p.set('method', method);
    if (targetType) p.set('target_type', targetType);
    if (noveltyFlag) p.set('novelty_flag', noveltyFlag);
    if (dateFrom) p.set('date_from', dateFrom);
    if (dateTo) p.set('date_to', dateTo);
    p.set('learning_ready', String(learningReadyOnly));
    p.set('sort', sort);
    return p.toString();
  }, [subject, gradeBand, difficulty, topic, method, targetType, noveltyFlag, dateFrom, dateTo, learningReadyOnly, sort]);

  useEffect(() => {
    if (searchMode !== 'list') return;
    setLoading(true);
    fetch(apiUrl(`/api/questions${qs ? '?' + qs : ''}`))
      .then((r) => r.json())
      .then((d) => {
        setItems(d.items || []);
        setFacets(d.facets || { methods: [], topics: [], target_types: [], novelty_flags: [] });
      })
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

  async function handleDeleteQuestion(questionId: string) {
    if (!confirm(`确定删除题目 #${questionId.slice(0, 8)} 及所有相关数据？此操作不可恢复。`)) return;
    try {
      const res = await fetch(apiUrl(`/api/questions/${questionId}/delete`), { method: 'POST' });
      if (!res.ok) throw new Error(await res.text());
      purgeQuestionLocalRefs(questionId);
      setItems((prev) => prev.filter((it) => it.question_id !== questionId));
    } catch (e) {
      alert(`删除失败: ${e}`);
    }
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
        <label>排序
          <select value={sort} onChange={(e) => setSort(e.target.value as any)} style={{ marginLeft: 4 }}>
            <option value="recommended">推荐</option>
            <option value="recent">最新</option>
            <option value="popular">常见</option>
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={learningReadyOnly}
            onChange={(e) => setLearningReadyOnly(e.target.checked)}
          />
          只看已确认可学习题
        </label>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginTop: 12 }}>
        <label>方法
          <select value={method} onChange={(e) => setMethod(e.target.value)} style={{ marginLeft: 4, maxWidth: 180 }}>
            <option value="">全部</option>
            {facets.methods.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>主题
          <select value={topic} onChange={(e) => setTopic(e.target.value)} style={{ marginLeft: 4, maxWidth: 180 }}>
            <option value="">全部</option>
            {facets.topics.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>目标类型
          <select value={targetType} onChange={(e) => setTargetType(e.target.value)} style={{ marginLeft: 4, maxWidth: 180 }}>
            <option value="">全部</option>
            {facets.target_types.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>题型标签
          <select value={noveltyFlag} onChange={(e) => setNoveltyFlag(e.target.value)} style={{ marginLeft: 4, maxWidth: 180 }}>
            <option value="">全部</option>
            {facets.novelty_flags.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>从
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} style={{ marginLeft: 4 }} />
        </label>
        <label>至
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} style={{ marginLeft: 4 }} />
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
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <Link href={`/q/${it.question_id}`} style={{ color: '#0366d6', textDecoration: 'none', flex: 1 }}>
                  <strong>#{it.question_id.slice(0, 8)}</strong>
                  {' · '}
                  <div className="q-text-preview">
                    <RichText text={it.question_text || '(无文本)'} />
                  </div>
                </Link>
                <button
                  type="button"
                  onClick={() => handleDeleteQuestion(it.question_id)}
                  style={{ marginLeft: 8, fontSize: 12, color: '#b42318', cursor: 'pointer' }}
                >
                  删除
                </button>
              </div>
              <div style={muted}>
                {it.subject} · {it.grade_band} · 难度 {it.difficulty}
                {it.pattern_name && <> · 模式 <code>{it.pattern_name}</code></>}
                {it.seen_count > 1 && <> · 出现 {it.seen_count} 次</>}
                {it.learning_ready && <> · 已确认入库</>}
                {!it.learning_ready && <> · 待确认</>}
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                {it.topic_path.slice(0, 3).map((item) => (
                  <span key={item} style={badgeStyle('#eef5ff', '#245ea8')}>{item}</span>
                ))}
                {it.target_types.slice(0, 2).map((item) => (
                  <span key={item} style={badgeStyle('#eef8f0', '#1f7a3d')}>{item}</span>
                ))}
                {it.novelty_flags.slice(0, 2).map((item) => (
                  <span key={item} style={badgeStyle('#fff8e8', '#9a6700')}>{item}</span>
                ))}
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
            <li key={h.question_id + (h.solution_id ?? '')} style={{ marginBottom: 8 }}>
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

function badgeStyle(bg: string, fg: string): React.CSSProperties {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    padding: '2px 8px',
    borderRadius: 999,
    background: bg,
    color: fg,
    fontSize: 12,
  };
}
