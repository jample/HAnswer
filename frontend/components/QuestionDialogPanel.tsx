'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

import { RichText } from './MathText';
import { apiUrl } from '../lib/api';

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

export default function QuestionDialogPanel({
  questionId,
  solutionId,
  canOpen,
  collapsed,
  onToggleCollapse,
}: {
  questionId: string;
  solutionId: string | null;
  canOpen: boolean;
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const anchorKey = `${questionId}:${solutionId ?? ''}`;
  const autoCreatedRef = useRef<string | null>(null);

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [draft, setDraft] = useState('');
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const anchoredSessions = useMemo(
    () => sessions.filter((session) => (
      session.question_id === questionId && session.solution_id === solutionId
    )),
    [questionId, sessions, solutionId],
  );

  useEffect(() => {
    autoCreatedRef.current = null;
    setSelectedId(null);
    setDetail(null);
    setDraft('');
    setError(null);
  }, [anchorKey]);

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

  async function createSession() {
    if (!solutionId) return;
    setError(null);
    try {
      const res = await fetch(apiUrl('/api/dialog/sessions'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question_id: questionId, solution_id: solutionId }),
      });
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body: SessionDetail = await res.json();
      setDetail(body);
      setSelectedId(body.session.id);
      setSessions((prev) => [body.session, ...prev.filter((item) => item.id !== body.session.id)]);
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
      setSessions((prev) => [body.session, ...prev.filter((item) => item.id !== body.session.id)]);
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  }

  useEffect(() => {
    if (!canOpen) return;
    refreshSessions().catch(() => {});
  }, [anchorKey, canOpen]);

  useEffect(() => {
    if (!canOpen || loadingSessions) return;

    if (anchoredSessions.length === 0) {
      if (autoCreatedRef.current !== anchorKey) {
        autoCreatedRef.current = anchorKey;
        createSession().catch(() => {});
      }
      return;
    }

    const next = anchoredSessions.find((session) => session.id === selectedId) ?? anchoredSessions[0];
    if (!next) return;
    if (detail?.session.id === next.id) {
      if (selectedId !== next.id) setSelectedId(next.id);
      return;
    }
    loadSession(next.id).catch(() => {});
  }, [anchorKey, anchoredSessions, canOpen, detail?.session.id, loadingSessions, selectedId]);

  const lastAssistant = [...(detail?.messages || [])]
    .reverse()
    .find((message) => message.role === 'assistant');
  const followUps = lastAssistant?.metadata?.follow_up_suggestions || [];

  if (collapsed) {
    return (
      <section className="card question-dialog-panel collapsed">
        <button className="btn btn-secondary question-dialog-collapse-btn" onClick={onToggleCollapse}>
          展开
        </button>
        <div className="question-dialog-collapsed-label">追问</div>
        <div className="question-dialog-collapsed-sub">
          {canOpen ? '当前解法' : '等待答案'}
        </div>
      </section>
    );
  }

  if (!canOpen) {
    return (
      <section className="card question-dialog-panel">
        <div className="question-dialog-header">
          <div>
            <h2 style={{ margin: 0, fontSize: 20 }}>基于答案的追问</h2>
            <p className="dialog-inline-hint">
              完成当前解法后，这里会出现与该解法绑定的多轮对话面板。
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="card question-dialog-panel">
      <div className="question-dialog-header">
        <div>
          <h2 style={{ margin: 0, fontSize: 20 }}>基于当前解法的追问</h2>
          <p className="dialog-inline-hint">
            对话会锁定到当前解法，一边看答案和可视化，一边继续追问。
          </p>
        </div>
        <div className="question-dialog-actions">
          <button className="btn btn-secondary" onClick={() => createSession()} disabled={!solutionId}>
            新会话
          </button>
          <button className="btn btn-secondary question-dialog-collapse-btn" onClick={onToggleCollapse}>
            收起
          </button>
        </div>
      </div>

      {anchoredSessions.length > 1 && (
        <div className="dialog-session-tabs">
          {anchoredSessions.map((session, index) => (
            <button
              key={session.id}
              type="button"
              className={`dialog-session-tab${selectedId === session.id ? ' active' : ''}`}
              onClick={() => loadSession(session.id)}
            >
              {session.title || `会话 ${index + 1}`}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="banner banner-danger" style={{ marginTop: 14 }}>
          <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{error}</span>
        </div>
      )}

      {detail?.question_context && (
        <div className="dialog-anchor-card">
          <div className="dialog-chip-row">
            <span className="dialog-chip">{detail.question_context.subject}</span>
            <span className="dialog-chip">{detail.question_context.grade_band}</span>
            <span className="dialog-chip">难度 {detail.question_context.difficulty}</span>
            <span className="dialog-chip">状态 {detail.question_context.status}</span>
          </div>

          {detail.question_context.answer_anchor && (
            <div style={{ marginTop: 10, color: 'var(--text-secondary)', fontSize: 13 }}>
              {detail.question_context.answer_anchor.title}
            </div>
          )}

          {detail.question_context.answer_context?.method_pattern?.name_cn && (
            <div style={{ marginTop: 8, color: 'var(--text-secondary)', fontSize: 13 }}>
              方法模式: <strong>{detail.question_context.answer_context.method_pattern.name_cn}</strong>
            </div>
          )}
        </div>
      )}

      {detail?.memory && (
        <div className="dialog-memory-stack">
          <MemoryCard title="滚动摘要" items={detail.memory.summary ? [detail.memory.summary] : []} empty="发送几轮后会自动形成摘要。" />
          <MemoryCard title="关键事实" items={detail.memory.key_facts} empty="暂无关键事实。" />
          <MemoryCard title="待继续问题" items={detail.memory.open_questions} empty="暂无待继续问题。" />
        </div>
      )}

      <div className="dialog-thread-card" style={{ marginTop: 14 }}>
        <div className="dialog-thread">
          {loadingDetail && <div style={{ color: 'var(--muted)' }}>加载对话中…</div>}
          {!loadingDetail && detail && detail.messages.length === 0 && (
            <div style={{ color: 'var(--muted)' }}>
              可以继续追问这一步为什么成立、还能不能换一种解法、如何识别同类题，或结合右侧答案继续细看。
            </div>
          )}
          {!loadingDetail && !detail && !loadingSessions && (
            <div style={{ color: 'var(--muted)' }}>
              正在为当前解法准备对话上下文…
            </div>
          )}
          {detail?.messages.map((message) => (
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
            <div className="dialog-inline-hint" style={{ margin: 0 }}>
              这里的上下文始终绑定当前解法，不会脱离右侧答案和可视化。
            </div>
            <button className="btn btn-primary" onClick={sendMessage} disabled={!draft.trim() || sending || !detail}>
              {sending ? '发送中…' : '发送'}
            </button>
          </div>
        </div>
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