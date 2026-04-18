'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

type KPNode = {
  id: string;
  parent_id: string | null;
  name_cn: string;
  path_cached: string;
  subject: string;
  grade_band: string;
  status: 'live' | 'pending';
  seen_count: number;
};

type PendingPayload = {
  kps: KPNode[];
  patterns: {
    id: string; name_cn: string; subject: string; grade_band: string;
    when_to_use: string; seen_count: number;
  }[];
};

type QuestionHit = {
  question_id: string;
  subject: string;
  grade_band: string;
  difficulty: number;
  question_text: string;
  weight: number | null;
};

type PatternAgg = {
  id: string;
  name_cn: string;
  subject: string;
  grade_band: string;
  status: 'live' | 'pending';
  co_occurrence: number;
  weight_sum: number;
};

type KpDetail = {
  kp: KPNode;
  questions: QuestionHit[];
  patterns: PatternAgg[];
};

const muted = { color: '#888', fontSize: 12 } as const;

export default function KnowledgePage() {
  const [tab, setTab] = useState<'tree' | 'pending' | 'prompts'>('tree');
  const [subject, setSubject] = useState('');
  const [gradeBand, setGradeBand] = useState('');
  const [nodes, setNodes] = useState<KPNode[]>([]);
  const [pending, setPending] = useState<PendingPayload>({ kps: [], patterns: [] });
  const [prompts, setPrompts] = useState<any[]>([]);
  const [selectedKpId, setSelectedKpId] = useState<string | null>(null);
  const [kpDetail, setKpDetail] = useState<KpDetail | null>(null);

  const reloadTree = useCallback(() => {
    const p = new URLSearchParams();
    if (subject) p.set('subject', subject);
    if (gradeBand) p.set('grade_band', gradeBand);
    fetch(`/api/knowledge/tree${p.toString() ? '?' + p : ''}`)
      .then((r) => r.json())
      .then((d) => setNodes(d.nodes || []));
  }, [subject, gradeBand]);

  const reloadPending = useCallback(() => {
    fetch('/api/knowledge/pending').then((r) => r.json()).then(setPending);
  }, []);

  useEffect(() => {
    if (tab === 'tree') reloadTree();
    if (tab === 'pending') reloadPending();
    if (tab === 'prompts') {
      fetch('/api/admin/prompts').then((r) => r.json()).then((d) => setPrompts(d.prompts || []));
    }
  }, [tab, reloadTree, reloadPending]);

  useEffect(() => {
    if (!selectedKpId) { setKpDetail(null); return; }
    fetch(`/api/knowledge/kp/${selectedKpId}/detail`)
      .then((r) => r.json())
      .then((d) => setKpDetail(d.kp ? d as KpDetail : null))
      .catch(() => setKpDetail(null));
  }, [selectedKpId]);

  async function act(kind: 'kp' | 'pattern', id: string, action: 'promote' | 'reject') {
    const res = await fetch(`/api/knowledge/${action}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ kind, id }),
    });
    if (res.ok) reloadPending();
  }

  const grouped = useMemo(() => groupByParent(nodes), [nodes]);

  return (
    <section>
      <h1>知识库</h1>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <TabButton active={tab === 'tree'} onClick={() => setTab('tree')}>知识点树</TabButton>
        <TabButton active={tab === 'pending'} onClick={() => setTab('pending')}>
          待审核 ({pending.kps.length + pending.patterns.length})
        </TabButton>
        <TabButton active={tab === 'prompts'} onClick={() => setTab('prompts')}>Prompt 注册表</TabButton>
      </div>

      {tab === 'tree' && (
        <>
          <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
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
          </div>
          <div style={{
            display: 'grid', gap: 16,
            gridTemplateColumns: 'minmax(260px, 1fr) minmax(260px, 2fr)',
          }}>
            <div style={{ borderRight: '1px solid #eee', paddingRight: 12 }}>
              <TreeView
                nodes={nodes} grouped={grouped}
                selectedId={selectedKpId}
                onSelect={setSelectedKpId}
              />
            </div>
            <div>
              <DetailPanel detail={kpDetail} />
            </div>
          </div>
        </>
      )}

      {tab === 'pending' && (
        <>
          <h2>待审核知识点 (LLM 提议)</h2>
          {pending.kps.length === 0 ? <p style={muted}>无待审核知识点。</p> : (
            <ul style={{ listStyle: 'none', paddingLeft: 0 }}>
              {pending.kps.map((k) => (
                <li key={k.id} style={rowStyle}>
                  <div><strong>{k.path_cached}</strong> · {k.subject} · {k.grade_band}</div>
                  <div style={muted}>出现 {k.seen_count} 次</div>
                  <div style={{ marginTop: 6, display: 'flex', gap: 6 }}>
                    <button onClick={() => act('kp', k.id, 'promote')}>提升为 live</button>
                    <button onClick={() => act('kp', k.id, 'reject')}>拒绝</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <h2>待审核方法模式</h2>
          {pending.patterns.length === 0 ? <p style={muted}>无待审核模式。</p> : (
            <ul style={{ listStyle: 'none', paddingLeft: 0 }}>
              {pending.patterns.map((p) => (
                <li key={p.id} style={rowStyle}>
                  <div><strong>{p.name_cn}</strong> · {p.subject} · {p.grade_band}</div>
                  <div style={muted}>{p.when_to_use}</div>
                  <div style={muted}>出现 {p.seen_count} 次</div>
                  <div style={{ marginTop: 6, display: 'flex', gap: 6 }}>
                    <button onClick={() => act('pattern', p.id, 'promote')}>提升为 live</button>
                    <button onClick={() => act('pattern', p.id, 'reject')}>拒绝</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {tab === 'prompts' && (
        <>
          <p style={muted}>LLM 提示词的版本与设计决策 (§7.1)。</p>
          <table cellPadding={6} style={{ borderCollapse: 'collapse', border: '1px solid #ddd' }}>
            <thead>
              <tr style={{ background: '#f6f6f6' }}>
                <th align="left">name</th>
                <th align="left">version</th>
                <th align="left">设计决策</th>
                <th align="left">purpose</th>
              </tr>
            </thead>
            <tbody>
              {prompts.map((p) => (
                <tr key={p.name} style={{ borderTop: '1px solid #eee' }}>
                  <td><code>{p.name}</code></td>
                  <td><code>{p.version}</code></td>
                  <td>{p.design_decisions}</td>
                  <td style={{ maxWidth: 420 }}>{p.purpose}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '6px 12px',
        border: '1px solid #ddd',
        background: active ? '#eef5ff' : '#fff',
        borderRadius: 4,
        cursor: 'pointer',
      }}
    >
      {children}
    </button>
  );
}

function groupByParent(nodes: KPNode[]): Map<string | null, KPNode[]> {
  const m = new Map<string | null, KPNode[]>();
  for (const n of nodes) {
    const k = n.parent_id;
    const arr = m.get(k) || [];
    arr.push(n);
    m.set(k, arr);
  }
  for (const arr of m.values()) arr.sort((a, b) => a.name_cn.localeCompare(b.name_cn, 'zh'));
  return m;
}

function TreeView({ nodes, grouped, selectedId, onSelect }: {
  nodes: KPNode[];
  grouped: Map<string | null, KPNode[]>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  if (nodes.length === 0) return <p style={muted}>未种子化知识点。运行 <code>python -m scripts.seed_knowledge</code>。</p>;
  return <NodeList parent={null} grouped={grouped} depth={0} selectedId={selectedId} onSelect={onSelect} />;
}

function NodeList({ parent, grouped, depth, selectedId, onSelect }: {
  parent: string | null;
  grouped: Map<string | null, KPNode[]>;
  depth: number;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const list = grouped.get(parent) || [];
  if (list.length === 0) return null;
  return (
    <ul style={{ listStyle: 'none', paddingLeft: depth === 0 ? 0 : 16 }}>
      {list.map((n) => {
        const isSel = n.id === selectedId;
        return (
          <li key={n.id} style={{ margin: '2px 0' }}>
            <button
              type="button"
              onClick={() => onSelect(isSel ? null : n.id)}
              style={{
                background: isSel ? '#eef5ff' : 'transparent',
                border: 'none', padding: '2px 4px', cursor: 'pointer',
                textAlign: 'left',
              }}
            >
              <span style={{
                color: n.status === 'live' ? '#0a7c3b' : '#b07c00',
                fontWeight: depth === 0 ? 600 : 400,
              }}>
                {n.name_cn}
              </span>
              <span style={{ ...muted, marginLeft: 6 }}>
                {n.subject}·{n.grade_band}
                {n.status === 'pending' && ' · 待审核'}
                {n.seen_count > 0 && ` · 关联 ${n.seen_count}`}
              </span>
            </button>
            <NodeList parent={n.id} grouped={grouped} depth={depth + 1}
              selectedId={selectedId} onSelect={onSelect} />
          </li>
        );
      })}
    </ul>
  );
}

function DetailPanel({ detail }: { detail: KpDetail | null }) {
  if (!detail) {
    return <p style={muted}>选择左侧一个知识点以查看相关题目与方法模式。</p>;
  }
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>{detail.kp.path_cached}</h2>
      <p style={muted}>
        {detail.kp.subject} · {detail.kp.grade_band} · {detail.kp.status}
        {' · '}关联 {detail.kp.seen_count} 次
      </p>

      <h3>相关题目 ({detail.questions.length})</h3>
      {detail.questions.length === 0 ? (
        <p style={muted}>暂无关联题目。</p>
      ) : (
        <ul style={{ listStyle: 'none', paddingLeft: 0 }}>
          {detail.questions.map((q) => (
            <li key={q.question_id} style={{ padding: '4px 0', borderBottom: '1px solid #eee' }}>
              <Link href={`/q/${q.question_id}`} style={{ color: '#0366d6', textDecoration: 'none' }}>
                {q.question_text.slice(0, 60) || '(无文本)'}
                {q.question_text.length > 60 && '…'}
              </Link>
              <div style={muted}>
                {q.subject}·{q.grade_band} · 难度 {q.difficulty}
                {q.weight != null && ` · 权重 ${q.weight.toFixed(2)}`}
              </div>
            </li>
          ))}
        </ul>
      )}

      <h3>共现方法模式 ({detail.patterns.length})</h3>
      {detail.patterns.length === 0 ? (
        <p style={muted}>暂无共现模式。</p>
      ) : (
        <ul style={{ listStyle: 'none', paddingLeft: 0 }}>
          {detail.patterns.map((p) => (
            <li key={p.id} style={{ padding: '4px 0' }}>
              <strong style={{ color: p.status === 'live' ? '#0a7c3b' : '#b07c00' }}>
                {p.name_cn}
              </strong>
              <span style={muted}>
                {' · '}{p.subject}·{p.grade_band} · 共现 {p.co_occurrence} 题
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const rowStyle: React.CSSProperties = {
  padding: '8px 0',
  borderBottom: '1px solid #eee',
};
