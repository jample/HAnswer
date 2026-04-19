'use client';

import Link from 'next/link';
import { Suspense, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';

import { RichText } from '../../components/MathText';
import { apiUrl } from '../../lib/api';

type SessionSummary = {
  id: string;
  question_id: string | null;
  solution_id: string | null;
  title: string;
  latest_summary: string;
  key_facts: string[];
  open_questions: string[];
  last_message_at: string;
  created_at: string;
};

type Message = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  sequence_no: number;
  content: string;
  metadata?: { follow_up_suggestions?: string[]; error?: boolean } | null;
  created_at: string;
};

type Memory = {
  summary: string;
  key_facts: string[];
  open_questions: string[];
};

type QuestionContext = {
  question_id: string;
  solution_id: string | null;
  subject: string;
  grade_band: string;
  difficulty: number;
  status: string;
  answer_anchor?: {
    solution_id: string | null;
    title: string;
    status: string;
    has_answer: boolean;
    anchor_scope: string;
  };
  parsed_question: {
    topic_path: string[];
    question_text: string;
    given: string[];
    find: string[];
    diagram_description: string;
    tags: string[];
  };
  answer_context?: {
    key_points_of_question?: string[];
    key_points_of_answer?: string[];
    method_pattern?: {
      name_cn: string;
      when_to_use: string;
      general_procedure: string[];
      pitfalls: string[];
    };
  };
};

type SessionDetail = {
  session: SessionSummary;
  messages: Message[];
  memory: Memory;
  question_context: QuestionContext | null;
};

export default function DialogPage() {
  return (
    <Suspense fallback={<section className="card" style={{ color: 'var(--muted)' }}>加载对话页…</section>}>
      <DialogPageInner />
    </Suspense>
  );
}

function DialogPageInner() {
  const searchParams = useSearchParams();
  const sessionIdFromQuery = searchParams.get('sessionId');
  const questionIdFromQuery = searchParams.get('questionId');
  const solutionIdFromQuery = searchParams.get('solutionId');
  const autoCreatedRef = useRef(false);

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(sessionIdFromQuery);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [draft, setDraft] = useState('');
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refreshSessions() {
    setLoadingSessions(true);
    try {
      const res = await fetch(apiUrl('/api/dialog/sessions'));
      const body = await res.json();
      setSessions(body.sessions || []);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingSessions(false);
    }
  }

  async function loadSession(id: string) {
    setLoadingDetail(true);
    setError(null);
    try {
      const res = await fetch(apiUrl(`/api/dialog/sessions/${id}`));
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body: SessionDetail = await res.json();
      setDetail(body);
      setSelectedId(id);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingDetail(false);
    }
  }

  async function createSession(payload: { title?: string; question_id?: string | null; solution_id?: string | null }) {
    setError(null);
    try {
      const res = await fetch(apiUrl('/api/dialog/sessions'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body: SessionDetail = await res.json();
      setDetail(body);
      setSelectedId(body.session.id);
      setSessions((prev) => [body.session, ...prev.filter((x) => x.id !== body.session.id)]);
    } catch (e) {
      setError(String(e));
    }
  }

  async function sendMessage() {
    if (!selectedId || !draft.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch(apiUrl(`/api/dialog/sessions/${selectedId}/messages`), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ content: draft }),
      });
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body = await res.json();
      setDraft('');
      setDetail({
        session: body.session,
        messages: body.messages,
        memory: body.memory,
        question_context: body.question_context,
      });
      setSessions((prev) => [body.session, ...prev.filter((x) => x.id !== body.session.id)]);
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  }

  useEffect(() => {
    refreshSessions().catch(() => {});
  }, []);

  useEffect(() => {
    if (loadingSessions) return;

    if (sessionIdFromQuery && sessions.some((item) => item.id === sessionIdFromQuery)) {
      if (selectedId !== sessionIdFromQuery) {
        loadSession(sessionIdFromQuery).catch(() => {});
      }
      return;
    }

    if (questionIdFromQuery) {
      const existing = sessions.find((item) => (
        item.question_id === questionIdFromQuery
        && (!solutionIdFromQuery || item.solution_id === solutionIdFromQuery)
      ));
      if (existing) {
        if (selectedId !== existing.id) {
          loadSession(existing.id).catch(() => {});
        }
        return;
      }
      if (!autoCreatedRef.current) {
        autoCreatedRef.current = true;
        createSession({ question_id: questionIdFromQuery, solution_id: solutionIdFromQuery }).catch(() => {});
      }
      return;
    }

    if (!selectedId && sessions[0]) {
      loadSession(sessions[0].id).catch(() => {});
    }
  }, [loadingSessions, questionIdFromQuery, selectedId, sessionIdFromQuery, sessions, solutionIdFromQuery]);

  const lastAssistant = [...(detail?.messages || [])].reverse().find((msg) => msg.role === 'assistant');
  const followUps = lastAssistant?.metadata?.follow_up_suggestions || [];

  return (
    <section className="dialog-layout">
      <aside className="dialog-sidebar card">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22 }}>多轮对话</h1>
            <p style={{ margin: '4px 0 0', color: 'var(--muted)', fontSize: 13 }}>
              会话会保留摘要记忆、关键事实与追问历史。
            </p>
          </div>
          <button className="btn btn-secondary" onClick={() => createSession({})}>新建</button>
        </div>

        {questionIdFromQuery && (
          <button
            className="btn btn-secondary"
            style={{ width: '100%', marginTop: 12, justifyContent: 'center' }}
            onClick={() => createSession({ question_id: questionIdFromQuery, solution_id: solutionIdFromQuery })}
          >
            基于当前解法新建对话
          </button>
        )}

        <div style={{ marginTop: 16, display: 'grid', gap: 10 }}>
          {loadingSessions && <div style={{ color: 'var(--muted)' }}>加载会话中…</div>}
          {!loadingSessions && sessions.length === 0 && (
            <div style={{ color: 'var(--muted)' }}>还没有对话。可以先新建一个。</div>
          )}
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={`dialog-session-card${selectedId === session.id ? ' active' : ''}`}
              onClick={() => loadSession(session.id)}
            >
              <div className="dialog-session-title">{session.title}</div>
              <div className="dialog-session-meta">
                {session.question_id
                  ? `题目 ${session.question_id.slice(0, 8)}${session.solution_id ? ` · 解法 ${session.solution_id.slice(0, 8)}` : ''}`
                  : '自由学习对话'}
              </div>
              <div className="dialog-session-preview">
                {session.latest_summary || '暂无摘要，发送第一条消息后会建立记忆。'}
              </div>
            </button>
          ))}
        </div>
      </aside>

      <div className="dialog-main">
        {error && (
          <div className="banner banner-danger">
            <span>⚠️</span>
            <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{error}</span>
          </div>
        )}

        {detail ? (
          <>
            <div className="card" style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <h2 style={{ margin: 0, fontSize: 20 }}>{detail.session.title}</h2>
                  <p style={{ margin: '6px 0 0', color: 'var(--muted)', fontSize: 13 }}>
                    {detail.session.question_id ? (
                      <>
                        绑定题目 <code>{detail.session.question_id}</code>
                        {detail.session.solution_id && (
                          <>
                            {' · '}锚定解法 <code>{detail.session.solution_id}</code>
                          </>
                        )}
                        {' · '}
                        <Link href={`/q/${detail.session.question_id}`}>回到解答页</Link>
                      </>
                    ) : '自由学习会话'}
                  </p>
                </div>
                <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                  最后更新 {new Date(detail.session.last_message_at).toLocaleString()}
                </div>
              </div>

              {detail.question_context && (
                <div className="dialog-anchor-card">
                  <div className="dialog-chip-row">
                    <span className="dialog-chip">{detail.question_context.subject}</span>
                    <span className="dialog-chip">{detail.question_context.grade_band}</span>
                    <span className="dialog-chip">难度 {detail.question_context.difficulty}</span>
                    <span className="dialog-chip">状态 {detail.question_context.status}</span>
                  </div>
                  <div style={{ marginTop: 10 }}>
                    <strong>题目上下文</strong>
                    <div style={{ marginTop: 8 }}>
                      <RichText text={detail.question_context.parsed_question.question_text} />
                    </div>
                  </div>
                  {detail.question_context.answer_anchor && (
                    <div style={{ marginTop: 12, padding: 12, borderRadius: 12, background: 'rgba(15, 23, 42, 0.04)' }}>
                      <div style={{ fontWeight: 600 }}>对话锚定答案</div>
                      <div style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
                        {detail.question_context.answer_anchor.title}
                        {' · '}状态 {detail.question_context.answer_anchor.status}
                      </div>
                    </div>
                  )}
                  {detail.question_context.answer_context?.method_pattern?.name_cn && (
                    <div style={{ marginTop: 10, color: 'var(--text-secondary)' }}>
                      方法模式: <strong>{detail.question_context.answer_context.method_pattern.name_cn}</strong>
                    </div>
                  )}
                </div>
              )}

              <div className="dialog-memory-grid">
                <MemoryCard title="滚动摘要" items={detail.memory.summary ? [detail.memory.summary] : []} empty="发送几轮后会自动形成摘要。" />
                <MemoryCard title="关键事实" items={detail.memory.key_facts} empty="暂无关键事实。" />
                <MemoryCard title="待继续问题" items={detail.memory.open_questions} empty="暂无待继续问题。" />
              </div>
            </div>

            <div className="card dialog-thread-card">
              <div className="dialog-thread">
                {loadingDetail && <div style={{ color: 'var(--muted)' }}>加载对话中…</div>}
                {!loadingDetail && detail.messages.length === 0 && (
                  <div style={{ color: 'var(--muted)' }}>
                    这里还没有消息。可以直接追问步骤原因、换一种讲法、相似题、知识点联系等。
                  </div>
                )}
                {detail.messages.map((message) => (
                  <div key={message.id} className={`dialog-bubble ${message.role}`}>
                    <div className="dialog-bubble-role">
                      {message.role === 'user' ? '你' : message.role === 'assistant' ? 'HAnswer' : '系统'}
                    </div>
                    <div className="dialog-bubble-content">
                      <RichText text={message.content} />
                    </div>
                  </div>
                ))}
              </div>

              {followUps.length > 0 && (
                <div className="dialog-followups">
                  {followUps.map((item) => (
                    <button
                      key={item}
                      type="button"
                      className="dialog-followup-chip"
                      onClick={() => setDraft(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              )}

              <div className="dialog-composer">
                <textarea
                  rows={4}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="继续追问：为什么这一步成立？还能换一种方法吗？这类题怎么识别？"
                />
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
                  <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                    后端会缓存摘要记忆和最近对话，减少每轮重复上下文。
                  </div>
                  <button className="btn btn-primary" onClick={sendMessage} disabled={!draft.trim() || sending}>
                    {sending ? '发送中…' : '发送'}
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="card" style={{ color: 'var(--muted)' }}>
            选择一个已有对话，或新建一个会话开始追问。
          </div>
        )}
      </div>
    </section>
  );
}

function MemoryCard({
  title,
  items,
  empty,
}: {
  title: string;
  items: string[];
  empty: string;
}) {
  return (
    <div className="dialog-memory-card">
      <div style={{ fontWeight: 700, marginBottom: 8 }}>{title}</div>
      {items.length === 0 ? (
        <div style={{ color: 'var(--muted)', fontSize: 13 }}>{empty}</div>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {items.map((item) => (
            <li key={item}>
              <RichText text={item} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
