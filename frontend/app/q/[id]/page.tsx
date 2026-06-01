'use client';

import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { TeX, RichText } from '../../../components/MathText';
import QuestionDialogPanel from '../../../components/QuestionDialogPanel';
import VizSandbox from '../../../components/VizSandbox';
import type { VizParam } from '../../../components/vizCommon';
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
  const purgeQuestionLocalRefs = useCallback((questionId: string) => {
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
  }, []);
  const redirectToLibrary = useCallback(() => {
    purgeQuestionLocalRefs(params.id);
    window.location.replace('/library');
  }, [params.id, purgeQuestionLocalRefs]);
  const dialogResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const latestResumeRef = useRef<{ key: string; body: any } | null>(null);
  const lastResumeFingerprintRef = useRef('');
  const unchangedPollCountRef = useRef(0);
  const [events, setEvents] = useState<AnyEv[]>([]);
  const [done, setDone] = useState(false);
  const [initial, setInitial] = useState<any | null>(null);
  const [resumeReady, setResumeReady] = useState(false);
  const [running, setRunning] = useState(false);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [visualizationPlan, setVisualizationPlan] = useState<any | null>(null);
  const [stageReviews, setStageReviews] = useState<StageReview[]>([]);
  const [solutions, setSolutions] = useState<SolutionSummary[]>([]);
  const [currentSolutionId, setCurrentSolutionId] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [creatingSolution, setCreatingSolution] = useState(false);
  const [stageActionPending, setStageActionPending] = useState<string | null>(null);
  const [stageNoteDrafts, setStageNoteDrafts] = useState<Record<string, string>>({});
  const [dialogLlmBusy, setDialogLlmBusy] = useState(false);
  const [dialogCollapsed, setDialogCollapsed] = useState(false);
  const [dialogPanelWidth, setDialogPanelWidth] = useState(380);
  const [toast, setToast] = useState<{ message: string; kind: 'info' | 'success' | 'error' } | null>(null);
  const [deletingQuestion, setDeletingQuestion] = useState(false);

  const showToast = useCallback((message: string, kind: 'info' | 'success' | 'error' = 'info') => {
    setToast({ message, kind });
  }, []);
  const dismissToast = useCallback(() => setToast(null), []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(dismissToast, 5000);
    return () => clearTimeout(timer);
  }, [toast, dismissToast]);

  const withSolution = useCallback((path: string, solutionId?: string | null) => {
    if (!solutionId) return path;
    const sep = path.includes('?') ? '&' : '?';
    return `${path}${sep}solution_id=${encodeURIComponent(solutionId)}`;
  }, []);
  const resumeKey = `${params.id}:${currentSolutionId ?? 'current'}`;

  useEffect(() => {
    if (deletingQuestion) return;
    const target = withSolution(`/api/questions/${params.id}`, currentSolutionId);
    fetch(apiUrl(target)).then(async (r) => {
      if (r.status === 404) {
        redirectToLibrary();
        return null;
      }
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    }).then((body) => {
      if (!body) return;
      setInitial(body);
      setVisualizationPlan(body?.visualization_plan ?? null);
      setSolutions(Array.isArray(body?.solutions) ? body.solutions : []);
      if (typeof body?.current_solution_id === 'string') {
        setCurrentSolutionId((prev) => prev ?? body.current_solution_id);
      }
    }).catch(() => {});
  }, [currentSolutionId, deletingQuestion, params.id, redirectToLibrary, withSolution]);

  useEffect(() => {
    try {
      const rawWidth = window.localStorage.getItem('hanswer.answer.dialog.width');
      const rawCollapsed = window.localStorage.getItem('hanswer.answer.dialog.collapsed');
      const parsedWidth = Number(rawWidth);
      if (Number.isFinite(parsedWidth)) {
        setDialogPanelWidth(Math.max(320, Math.min(560, parsedWidth)));
      }
      if (rawCollapsed === 'true') {
        setDialogCollapsed(true);
      }
    } catch {
      /* noop */
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem('hanswer.answer.dialog.width', String(dialogPanelWidth));
      window.localStorage.setItem('hanswer.answer.dialog.collapsed', dialogCollapsed ? 'true' : 'false');
    } catch {
      /* noop */
    }
  }, [dialogCollapsed, dialogPanelWidth]);

  const loadResume = useCallback(async () => {
    if (deletingQuestion) return null;
    const res = await fetch(apiUrl(withSolution(`/api/answer/${params.id}/resume`, currentSolutionId)));
    if (res.status === 404) {
      redirectToLibrary();
      return null;
    }
    if (!res.ok) return null;
    const body = await res.json();
    latestResumeRef.current = { key: resumeKey, body };
    const replay = resumeToEvents(body);
    setEvents(replay);
    // Check both body.status AND job.error/job.done — the latter catches
    // cases where solution.status wasn't updated but job state recorded the error.
    const hasError = body?.status === 'error' || Boolean(body?.job?.error);
    const jobDone = Boolean(body?.job?.done);
    const incomingStageReviews = Array.isArray(body?.stage_reviews) ? body.stage_reviews : [];
    const awaitingReview = hasPendingStageReview(incomingStageReviews);
    setDone(Boolean(body?.complete) || hasError || jobDone);
    setRunning(Boolean(body?.job?.running) && !hasError && !jobDone && !awaitingReview);
    setJobStage(typeof body?.job?.stage === 'string' ? body.job.stage : body?.status ?? null);
    setPipeline(body?.pipeline ?? null);
    setVisualizationPlan(body?.visualization_plan ?? null);
    setStageReviews(incomingStageReviews);
    setSolutions(Array.isArray(body?.solutions) ? body.solutions : []);
    if (typeof body?.current_solution_id === 'string') {
      setCurrentSolutionId((prev) => prev ?? body.current_solution_id);
    }
    return body;
  }, [currentSolutionId, deletingQuestion, params.id, redirectToLibrary, resumeKey, withSolution]);

  useEffect(() => {
    latestResumeRef.current = null;
    lastResumeFingerprintRef.current = '';
    unchangedPollCountRef.current = 0;
  }, [resumeKey]);

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
    autoStartedRef.current = false;
  }, [resumeKey]);

  useEffect(() => {
    if (!resumeReady || done || autoStartedRef.current) return;
    let cancelled = false;

    (async () => {
      try {
        const cached = latestResumeRef.current?.key === resumeKey ? latestResumeRef.current.body : null;
        const first = cached ?? await loadResume();
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
  }, [currentSolutionId, done, loadResume, params.id, resumeKey, resumeReady, withSolution]);

  useEffect(() => {
    if (!resumeReady || done || !running) return;
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    const scheduleNext = (body: any | null) => {
      const fingerprint = body ? buildResumeFingerprint(body) : '';
      if (fingerprint && fingerprint === lastResumeFingerprintRef.current) {
        unchangedPollCountRef.current += 1;
      } else {
        lastResumeFingerprintRef.current = fingerprint;
        unchangedPollCountRef.current = 0;
      }
      if (!cancelled) {
        timeoutId = setTimeout(
          poll,
          nextResumePollDelay(body, unchangedPollCountRef.current),
        );
      }
    };

    const poll = () => {
      loadResume().then((body) => {
        scheduleNext(body);
      }).catch(() => {
        scheduleNext(null);
      });
    };

    const cached = latestResumeRef.current?.key === resumeKey ? latestResumeRef.current.body : null;
    timeoutId = setTimeout(poll, nextResumePollDelay(cached, 0));
    return () => { cancelled = true; clearTimeout(timeoutId); };
  }, [done, loadResume, resumeKey, resumeReady, running]);

  const byName = useMemo(() => groupBy(events), [events]);
  const liveStatusPayload = useMemo(() => latestStatusPayload(byName), [byName]);
  const liveStatusMessage = typeof liveStatusPayload?.message === 'string' ? liveStatusPayload.message : null;

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
    if (restarting || stageActionPending || dialogLlmBusy) return;
    setRestarting(true);
    showToast(running ? '正在停止当前任务并重新开始解答…' : '正在重新开始解答…', 'info');
    try {
      const res = await fetch(apiUrl(withSolution(`/api/answer/${params.id}/start`, currentSolutionId)), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ force: running }),
      });
      if (!res.ok) throw new Error(await res.text());
      setEvents([]);
      setDone(false);
      setRunning(true);
      setJobStage('queued');
      setPipeline(null);
      await loadResume();
      showToast(running ? '当前任务已中断，新的解答任务已启动' : '解答已重新启动', 'success');
    } catch (e) {
      showToast(`重启失败: ${e}`, 'error');
    } finally {
      setRestarting(false);
    }
  }

  async function createNewSolution() {
    if (llmBusy) return;
    setCreatingSolution(true);
    showToast('正在创建新解法…', 'info');
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
      showToast('新解法已创建', 'success');
    } catch (e) {
      showToast(`创建解法失败: ${e}`, 'error');
    } finally {
      setCreatingSolution(false);
    }
  }

  const pendingReview = useMemo(
    () => getPendingStageReview(stageReviews),
    [stageReviews],
  );
  const llmBusy = running || restarting || creatingSolution || Boolean(stageActionPending) || dialogLlmBusy;
  const llmBusyMessage = dialogLlmBusy
    ? '当前正在进行追问调用，其他会触发 LLM 的操作已暂时锁定。'
    : llmBusy
      ? '当前有 LLM 调用在执行或排队，其他会触发 LLM 的操作已暂时锁定。'
      : null;
  const hasAnswerContent = Boolean(
    initial?.answer_package
    || byName.solution_step?.length
    || byName.method_pattern?.length
    || byName.key_points_of_answer?.length,
  );
  const canOpenDialog = Boolean(currentSolutionId && hasAnswerContent);
  const pageGridStyle = currentSolutionId
    ? ({ ['--dialog-panel-width' as string]: `${dialogPanelWidth}px` } as React.CSSProperties)
    : undefined;

  const handleDialogResize = useCallback((event: PointerEvent) => {
    const state = dialogResizeRef.current;
    if (!state) return;
    const nextWidth = Math.max(320, Math.min(560, state.startWidth - (event.clientX - state.startX)));
    setDialogPanelWidth(nextWidth);
  }, []);

  const stopDialogResize = useCallback(() => {
    dialogResizeRef.current = null;
    window.removeEventListener('pointermove', handleDialogResize);
    window.removeEventListener('pointerup', stopDialogResize);
  }, [handleDialogResize]);

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', handleDialogResize);
      window.removeEventListener('pointerup', stopDialogResize);
    };
  }, [handleDialogResize, stopDialogResize]);

  const startDialogResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (dialogCollapsed || window.innerWidth < 1024) return;
    event.preventDefault();
    dialogResizeRef.current = { startX: event.clientX, startWidth: dialogPanelWidth };
    window.addEventListener('pointermove', handleDialogResize);
    window.addEventListener('pointerup', stopDialogResize);
  }, [dialogCollapsed, dialogPanelWidth, handleDialogResize, stopDialogResize]);

  async function handleStageAction(stage: string, action: 'confirm' | 'rerun') {
    if (llmBusy) return;
    const key = `${stage}:${action}`;
    setStageActionPending(key);
    const label = stageLabel(stage);
    if (action === 'rerun') {
      setDone(false);
      setRunning(true);
      setJobStage(stage);
      setPipeline((prev) => optimisticPipelineForRerun(prev, stage));
      setEvents((prev) => [
        ...prev.filter((ev) => !(ev.name === 'status' && ev.data?.stage === stage)),
        {
          name: 'status',
          data: {
            stage,
            message: optimisticRerunMessage(stage),
          },
          ts: Date.now(),
        },
      ]);
    }
    showToast(`正在${action === 'rerun' ? '重跑' : '确认'}${label}…`, 'info');
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
      showToast(`${label}${action === 'rerun' ? '重跑' : '确认'}完成`, 'success');
    } catch (e) {
      setEvents((prev) => [...prev, {
        name: 'error',
        data: { message: String(e) },
        ts: Date.now(),
      }]);
      showToast(`${label}${action === 'rerun' ? '重跑' : '确认'}失败: ${e}`, 'error');
    } finally {
      setStageActionPending(null);
    }
  }

  async function handleDirectRerun(stage: string) {
    await handleStageAction(stage, 'rerun');
  }

  async function handleDeleteSolution(solutionId: string) {
    if (llmBusy) return;
    if (!confirm('确定删除此解法？相关可视化和索引数据将一并删除。')) return;
    showToast('正在删除解法…', 'info');
    try {
      const res = await fetch(
        apiUrl(`/api/questions/${params.id}/solutions/${solutionId}/delete`),
        { method: 'POST' },
      );
      if (!res.ok) {
        const body = await res.text();
        if (res.status === 409) { showToast('无法删除最后一个解法', 'error'); return; }
        throw new Error(body);
      }
      if (currentSolutionId === solutionId) setCurrentSolutionId(null);
      await loadResume();
      showToast('解法已删除', 'success');
    } catch (e) {
      showToast(`删除失败: ${e}`, 'error');
    }
  }

  async function handleDeleteVisualization(vizRef: string) {
    if (llmBusy) return;
    if (!currentSolutionId) return;
    if (!confirm(`确定删除可视化 "${vizRef}"？`)) return;
    showToast('正在删除可视化…', 'info');
    try {
      const res = await fetch(
        apiUrl(`/api/questions/${params.id}/solutions/${currentSolutionId}/visualizations/${encodeURIComponent(vizRef)}/delete`),
        { method: 'POST' },
      );
      if (!res.ok) throw new Error(await res.text());
      await loadResume();
      showToast('可视化已删除', 'success');
    } catch (e) {
      showToast(`删除失败: ${e}`, 'error');
    }
  }

  async function handleClearIndex() {
    if (llmBusy) return;
    if (!currentSolutionId) return;
    if (!confirm('确定清除当前解法的检索索引？此操作不可恢复。')) return;
    showToast('正在清除索引…', 'info');
    try {
      const res = await fetch(
        apiUrl(`/api/questions/${params.id}/solutions/${currentSolutionId}/index/clear`),
        { method: 'POST' },
      );
      if (!res.ok) throw new Error(await res.text());
      await loadResume();
      showToast('索引已清除', 'success');
    } catch (e) {
      showToast(`清除失败: ${e}`, 'error');
    }
  }

  async function handleDeleteQuestion() {
    if (llmBusy) return;
    if (!confirm('确定删除此题目及所有相关数据（解法、可视化、索引）？此操作不可恢复。')) return;
    setDeletingQuestion(true);
    setRunning(false);
    showToast('正在删除题目…', 'info');
    try {
      const res = await fetch(apiUrl(`/api/questions/${params.id}/delete`), { method: 'POST' });
      if (res.status === 404) {
        redirectToLibrary();
        return;
      }
      if (!res.ok) throw new Error(await res.text());
      redirectToLibrary();
    } catch (e) {
      setDeletingQuestion(false);
      showToast(`删除失败: ${e}`, 'error');
    }
  }

  return (
    <>
    {toast && <ToastNotification message={toast.message} kind={toast.kind} onDismiss={dismissToast} />}
    <section
      className={`qpage-grid${canOpenDialog ? ' with-dialog' : ''}${dialogCollapsed ? ' dialog-collapsed' : ''}`}
      style={pageGridStyle}
    >
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
      <button
        type="button"
        onClick={handleDeleteQuestion}
        disabled={deletingQuestion || llmBusy}
        style={{ fontSize: 12, marginBottom: 8, marginLeft: 8, color: '#b42318' }}
      >
        {deletingQuestion ? '删除中…' : '删除题目'}
      </button>
      <div style={{ marginBottom: 12 }}>
        {llmBusyMessage ? (
          <span style={mutedStyle}>{llmBusyMessage}</span>
        ) : canOpenDialog ? (
          <span style={mutedStyle}>右侧追问面板已绑定当前解法；窄屏时会自动折到下方。</span>
        ) : (
          <span style={mutedStyle}>完成“生成解答”后, 才能进入基于答案的多轮对话。</span>
        )}
      </div>
      <SolutionSwitcher
        solutions={solutions}
        currentSolutionId={currentSolutionId}
        creating={creatingSolution}
        actionLocked={llmBusy}
        onSelect={setCurrentSolutionId}
        onCreate={createNewSolution}
        onDelete={handleDeleteSolution}
      />
      {!done && (
        <p style={mutedStyle}>
          {liveStatusMessage
            ?? progressHeadline(pipeline)
            ?? statusLabel(jobStage)
            ?? (running ? '解答生成中…' : '正在启动后台解答任务…')}
        </p>
      )}
      <SolverLiveBanner
        running={running}
        restarting={restarting}
        jobStage={jobStage}
        pipeline={pipeline}
        statusPayload={liveStatusPayload}
        solutionStepCount={byName.solution_step?.length ?? 0}
      />
      {(running || restarting) && (
        <div style={{ marginBottom: 12 }}>
          <button
            className="btn btn-secondary"
            onClick={restartAnswer}
            disabled={restarting || Boolean(stageActionPending) || dialogLlmBusy}
          >
            {restarting ? '正在中断并重启…' : '停止当前任务并重新开始'}
          </button>
          <span style={{ ...mutedStyle, marginLeft: 10 }}>
            服务重启后恢复的旧任务也可以用这个按钮中断并重新启动。
          </span>
        </div>
      )}
      <GeminiProgress pipeline={pipeline} done={done} liveMessage={liveStatusMessage} />
      <StageRerunBoard
        currentSolutionId={currentSolutionId}
        stageReviews={stageReviews}
        noteDrafts={stageNoteDrafts}
        onNoteChange={(stage, value) => setStageNoteDrafts((prev) => ({ ...prev, [stage]: value }))}
        actionPending={stageActionPending}
        actionLocked={llmBusy}
        onRerun={handleDirectRerun}
        onClearIndex={handleClearIndex}
      />
      <StageReviewPanel
        review={pendingReview}
        parsed={initial?.parsed || null}
        noteValue={(pendingReview && stageNoteDrafts[pendingReview.stage] !== undefined)
          ? stageNoteDrafts[pendingReview.stage]
          : pendingReview?.review_note || ''}
        onNoteChange={(stage, value) => setStageNoteDrafts((prev) => ({ ...prev, [stage]: value }))}
        actionPending={stageActionPending}
        actionLocked={llmBusy}
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
        <VizPanel
          questionId={params.id}
          vizEvents={byName.visualization || []}
          visualizationPlan={visualizationPlan}
          fullWidth
          onDeleteViz={handleDeleteVisualization}
          actionsLocked={llmBusy}
        />
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
        <ErrorPanel events={byName.error} onRetry={restartAnswer} restarting={restarting} actionLocked={llmBusy} />
      )}
      </article>

      {currentSolutionId && !deletingQuestion && (
        <aside className="qpage-sidepanel">
          {canOpenDialog && !dialogCollapsed && (
            <div
              className="qpage-sidepanel-resizer"
              onPointerDown={startDialogResize}
              role="separator"
              aria-orientation="vertical"
              aria-label="调整追问面板宽度"
            />
          )}
          <QuestionDialogPanel
            questionId={params.id}
            solutionId={currentSolutionId}
            canOpen={canOpenDialog}
            llmBusy={llmBusy}
            onLlmBusyChange={setDialogLlmBusy}
            collapsed={dialogCollapsed}
            onToggleCollapse={() => setDialogCollapsed((prev) => !prev)}
          />
        </aside>
      )}
    </section>
    </>
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

function latestStatusPayload(byName: Partial<Record<SectionName, AnyEv[]>>): Record<string, any> | null {
  const items = byName.status;
  if (!items?.length) return null;
  const last = items[items.length - 1]?.data;
  return last && typeof last === 'object' ? last : null;
}

function latestStatus(byName: Partial<Record<SectionName, AnyEv[]>>): string | null {
  const last = latestStatusPayload(byName);
  return typeof last?.message === 'string' ? last.message : null;
}

function formatElapsed(totalSeconds: number | null): string | null {
  if (totalSeconds == null || totalSeconds < 0) return null;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}小时 ${minutes}分 ${seconds}秒`;
  }
  if (minutes > 0) {
    return `${minutes}分 ${seconds}秒`;
  }
  return `${seconds}秒`;
}

function buildResumeFingerprint(body: any): string {
  const stageReviews = Array.isArray(body?.stage_reviews)
    ? body.stage_reviews.map((item: any) => `${item.stage}:${item.review_status}:${item.artifact_version}`).join('|')
    : '';
  const plan = body?.visualization_plan ?? null;
  return JSON.stringify({
    status: body?.status ?? null,
    complete: Boolean(body?.complete),
    running: Boolean(body?.job?.running),
    stage: body?.job?.stage ?? null,
    done: Boolean(body?.job?.done),
    error: body?.job?.error ?? null,
    sections: Array.isArray(body?.sections) ? body.sections.length : 0,
    visualizations: Array.isArray(body?.visualizations) ? body.visualizations.length : 0,
    visualizationPlanSelectedId: plan?.selected_visualization?.id ?? plan?.selected_visualization_id ?? null,
    visualizationPlanCandidateCount: Array.isArray(plan?.visualizations) ? plan.visualizations.length : 0,
    stageReviews,
  });
}

function nextResumePollDelay(body: any, unchangedCount: number): number {
  const stage = typeof body?.job?.stage === 'string' ? body.job.stage : body?.status ?? null;
  let baseMs = 2500;
  if (stage === 'solving') {
    baseMs = 1800;
  } else if (stage === 'visualizing') {
    baseMs = 3000;
  } else if (stage === 'indexing') {
    baseMs = 4000;
  } else if (stage === 'queued') {
    baseMs = 2000;
  }

  const hidden = typeof document !== 'undefined' && document.visibilityState !== 'visible';
  const hiddenBaseMs = hidden ? Math.max(baseMs, 8000) : baseMs;
  const maxMs = hidden ? 15000 : 6000;
  return Math.min(hiddenBaseMs + unchangedCount * 1000, maxMs);
}

function optimisticRerunMessage(stage: string): string {
  const labels: Record<string, string> = {
    parsed: '正在重新解析题面。',
    solving: '正在重新生成解答。',
    visualizing: '正在重新生成可视化（重新规划规格并生成代码）。',
    indexing: '正在重新建立索引。',
  };
  return labels[stage] ?? '正在重跑当前阶段。';
}

function optimisticPipelineForRerun(pipeline: Pipeline | null, stage: string): Pipeline | null {
  if (!pipeline) return null;
  const stageOrder = ['parsed', 'solving', 'visualizing', 'indexing'];
  const stageIndex = stageOrder.indexOf(stage);
  if (stageIndex < 0) return pipeline;

  const steps = pipeline.steps.map((step) => {
    const idx = stageOrder.indexOf(step.key);
    if (idx < 0) return step;
    if (idx < stageIndex) {
      return { ...step, state: 'done' as const };
    }
    if (idx === stageIndex) {
      return { ...step, state: 'active' as const, review_status: null, artifact_version: 0 };
    }
    return { ...step, state: 'pending' as const, review_status: null, artifact_version: 0 };
  });

  return {
    ...pipeline,
    current_stage: stage,
    current_call: stageIndex + 1,
    completed_calls: stageIndex,
    visualizations_generated: stageIndex > stageOrder.indexOf('visualizing')
      ? pipeline.visualizations_generated
      : false,
    error: null,
    steps,
  };
}

function progressHeadline(pipeline: Pipeline | null): string | null {
  if (!pipeline) return null;
  if (pipeline.error) return pipeline.error;
  const active = pipeline.steps.find((step) => step.state === 'active');
  if (active) {
    return `LLM ${active.call_index}/${pipeline.total_calls} · ${active.label}`;
  }
  const review = pipeline.steps.find((step) => step.state === 'review');
  if (review) {
    return `等待人工确认 · LLM ${review.call_index}/${pipeline.total_calls} · ${review.label}`;
  }
  if (pipeline.completed_calls >= pipeline.total_calls) {
    return `LLM ${pipeline.total_calls}/${pipeline.total_calls} · 全部调用完成`;
  }
  return `LLM ${pipeline.completed_calls}/${pipeline.total_calls} · 等待下一阶段`;
}

function statusLabel(stage: string | null): string | null {
  if (!stage) return null;
  const labels: Record<string, string> = {
    parsed: '题目已解析，等待开始解答。',
    review_parse: '题面解析已完成，等待人工确认。',
    queued: '解答任务已排队。',
    solving: '正在生成教学型答案。',
    review_solve: '解答已生成，等待人工确认。',
    visualizing: '正在生成可视化规格并生成 GeoGebra 指令。',
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

function SolverLiveBanner({
  running,
  restarting,
  jobStage,
  pipeline,
  statusPayload,
  solutionStepCount,
}: {
  running: boolean;
  restarting: boolean;
  jobStage: string | null;
  pipeline: Pipeline | null;
  statusPayload: Record<string, any> | null;
  solutionStepCount: number;
}) {
  const solvingActive = Boolean(
    (running || restarting)
    && (
      jobStage === 'solving'
      || pipeline?.current_stage === 'solving'
      || statusPayload?.stage === 'solving'
    ),
  );
  const [startedAtMs, setStartedAtMs] = useState<number | null>(null);
  const [, setTicker] = useState(0);

  useEffect(() => {
    if (!solvingActive) {
      setStartedAtMs(null);
      return;
    }
    const waited = Number(statusPayload?.wait_elapsed_s);
    if (Number.isFinite(waited) && waited >= 0) {
      const inferredStart = Date.now() - waited * 1000;
      setStartedAtMs((prev) => {
        if (prev == null) return inferredStart;
        return Math.min(prev, inferredStart);
      });
      return;
    }
    setStartedAtMs((prev) => prev ?? Date.now());
  }, [solvingActive, statusPayload?.wait_elapsed_s]);

  useEffect(() => {
    if (!solvingActive) return;
    const timer = window.setInterval(() => {
      setTicker((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [solvingActive]);

  if (!solvingActive) return null;

  const elapsedSeconds = startedAtMs == null ? null : Math.max(0, Math.floor((Date.now() - startedAtMs) / 1000));
  const elapsedLabel = formatElapsed(elapsedSeconds);
  const waitingForFirstSection = solutionStepCount === 0 && !statusPayload?.heartbeat;
  const message = typeof statusPayload?.message === 'string'
    ? statusPayload.message
    : restarting
      ? '正在停止当前任务并重新启动解答。'
      : 'LLM 正在生成解答。';

  let detail = '正在准备首段答案内容。';
  if (statusPayload?.heartbeat) {
    detail = 'LLM 已开始推理，当前仍在等待第一个完整答案区块。';
  } else if (solutionStepCount > 0) {
    detail = `分步解答已显示 ${solutionStepCount} 步，后续内容会继续追加。`;
  } else if (waitingForFirstSection) {
    detail = '第一个完整区块尚未落盘，页面会在拿到可显示内容后立即刷新。';
  }

  return (
    <div style={{
      marginBottom: 14,
      padding: 14,
      borderRadius: 10,
      border: '1px solid #d5e4fb',
      background: 'linear-gradient(135deg, #f7fbff 0%, #eef5ff 100%)',
      boxShadow: '0 10px 24px rgba(36, 94, 168, 0.08)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 10px',
              borderRadius: 999,
              background: '#245ea8',
              color: '#fff',
              fontSize: 12,
              fontWeight: 700,
            }}>
              <span style={{ fontSize: 10 }}>●</span>
              生成解答进行中
            </span>
            {elapsedLabel && (
              <span style={{
                fontSize: 12,
                color: '#245ea8',
                fontWeight: 600,
              }}
              >
                已耗时 {elapsedLabel}
              </span>
            )}
          </div>
          <div style={{ marginTop: 8, fontSize: 15, fontWeight: 600, color: '#153e75', lineHeight: 1.45 }}>
            {message}
          </div>
          <div style={{ marginTop: 6, fontSize: 13, color: '#466489', lineHeight: 1.5 }}>
            {detail}
          </div>
        </div>
        <div style={{
          minWidth: 148,
          padding: '10px 12px',
          borderRadius: 8,
          background: '#fff',
          border: '1px solid #d5e4fb',
        }}>
          <div style={{ fontSize: 12, color: '#5d7290' }}>当前阶段</div>
          <div style={{ marginTop: 4, fontWeight: 700, color: '#153e75' }}>
            LLM {pipeline?.current_call ?? 2}/{pipeline?.total_calls ?? 4}
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: '#5d7290' }}>
            已显示步骤 {solutionStepCount}
          </div>
        </div>
      </div>
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
          <div style={{ fontWeight: 600 }}>LLM 调用进度</div>
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
                LLM {step.call_index}/4 · {step.label}
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
  actionLocked,
  onSelect,
  onCreate,
  onDelete,
}: {
  solutions: SolutionSummary[];
  currentSolutionId: string | null;
  creating: boolean;
  actionLocked: boolean;
  onSelect: (solutionId: string) => void;
  onCreate: () => void;
  onDelete: (solutionId: string) => void;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 600 }}>解法版本</div>
        <button className="btn btn-secondary" onClick={onCreate} disabled={creating || actionLocked}>
          {creating ? '创建中…' : '新建解法'}
        </button>
      </div>
      {!!solutions.length && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
          {solutions.map((solution) => {
            const active = solution.solution_id === currentSolutionId;
            const isLast = solutions.length === 1;
            return (
              <div key={solution.solution_id} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <button
                  type="button"
                  onClick={() => onSelect(solution.solution_id)}
                  disabled={actionLocked}
                  style={{
                    border: active ? '1px solid #245ea8' : '1px solid #d9d9d9',
                    background: active ? '#eef5ff' : '#fff',
                    color: active ? '#245ea8' : '#666',
                    borderRadius: 999,
                    padding: '6px 12px',
                    fontSize: 12,
                    cursor: actionLocked ? 'not-allowed' : 'pointer',
                    opacity: actionLocked ? 0.6 : 1,
                  }}
                >
                  {solution.title}
                  {solution.is_current ? ' · 当前' : ''}
                </button>
                {!isLast && (
                  <button
                    type="button"
                    onClick={() => onDelete(solution.solution_id)}
                    disabled={actionLocked}
                    style={{
                      border: '1px solid #f4c7c7',
                      background: '#fff',
                      color: '#b42318',
                      borderRadius: 4,
                      padding: '4px 8px',
                      fontSize: 11,
                      cursor: actionLocked ? 'not-allowed' : 'pointer',
                      opacity: actionLocked ? 0.6 : 1,
                    }}
                    title="删除此解法"
                  >
                    删除
                  </button>
                )}
              </div>
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
  actionLocked,
  onRerun,
  onClearIndex,
}: {
  currentSolutionId: string | null;
  stageReviews: StageReview[];
  noteDrafts: Record<string, string>;
  onNoteChange: (stage: string, value: string) => void;
  actionPending: string | null;
  actionLocked: boolean;
  onRerun: (stage: string) => void;
  onClearIndex: () => void;
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
          const disabled = actionLocked || (stage !== 'parsed' && !currentSolutionId);
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
              <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="btn btn-secondary" onClick={() => onRerun(stage)} disabled={disabled || pending}>
                  {pending ? '重跑中…' : `重跑${stageLabel(stage)}`}
                </button>
                {stage === 'indexing' && currentSolutionId && (
                  <button
                    className="btn btn-secondary"
                    onClick={onClearIndex}
                    disabled={actionLocked}
                    style={{ color: '#b42318' }}
                  >
                    清除索引
                  </button>
                )}
              </div>
              {stage === 'indexing' && (
                <div style={{ fontSize: 11, color: '#888', marginTop: 4 }}>
                  清除索引将删除检索单元和向量，可重新建立索引。
                </div>
              )}
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
    return `规格 ${summary.candidate_count || 0} 个 · 主规格 ${summary.selected_visualization_id || '-'} · 已生成 ${summary.visualization_count || 0} 个`;
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
  actionLocked,
  onConfirm,
  onRerun,
}: {
  review: StageReview | null;
  parsed: any | null;
  noteValue: string;
  onNoteChange: (stage: string, value: string) => void;
  actionPending: string | null;
  actionLocked: boolean;
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
        <button className="btn btn-secondary" onClick={() => onConfirm(review.stage)} disabled={confirming || rerunning || actionLocked}>
          {confirming ? '确认中…' : '确认并进入下一阶段'}
        </button>
        <button className="btn btn-secondary" onClick={() => onRerun(review.stage)} disabled={confirming || rerunning || actionLocked}>
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
  actionLocked,
}: {
  events: AnyEv[];
  onRetry: () => void;
  restarting: boolean;
  actionLocked: boolean;
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
        {isTimeout ? 'LLM 调用超时' : isServiceOverloaded ? 'LLM 服务繁忙' : '解答失败'}
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
        <button className="btn btn-secondary" onClick={onRetry} disabled={restarting || actionLocked}>
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
  questionId,
  vizEvents,
  visualizationPlan,
  fullWidth = false,
  onDeleteViz,
  actionsLocked = false,
}: {
  questionId?: string;
  vizEvents: AnyEv[];
  visualizationPlan?: any | null;
  fullWidth?: boolean;
  onDeleteViz?: (vizRef: string) => void;
  actionsLocked?: boolean;
}) {
  const [activeIdx, setActiveIdx] = useState(0);
  if (vizEvents.length === 0 && !visualizationPlan) {
    return (
      <div style={{
        padding: 12, border: '1px dashed #ddd', borderRadius: 6,
        color: '#999', fontSize: 13,
      }}>
        暂无可视化。生成中…
      </div>
    );
  }
  const orderedVizEvents = [...vizEvents].sort((a, b) => {
    const aDegraded = Boolean(a?.data?.degraded);
    const bDegraded = Boolean(b?.data?.degraded);
    if (aDegraded === bDegraded) return 0;
    return aDegraded ? 1 : -1;
  });
  const active = orderedVizEvents[Math.min(activeIdx, orderedVizEvents.length - 1)];
  const resolvedParams = active ? deriveVizParams(active.data.execution_payload, active.data.spec_json) : [];
  return (
    <div style={{
      border: '1px solid #e5e7eb',
      borderRadius: 12,
      background: '#fff',
      padding: fullWidth ? 16 : 8,
      boxShadow: fullWidth ? '0 1px 3px rgba(0,0,0,0.04)' : 'none',
    }}>
      {visualizationPlan && <VisualizationPlanCard plan={visualizationPlan} />}
      {vizEvents.length > 0 && (
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {orderedVizEvents.map((ev, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <button
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
              {i + 1}. {ev.data.degraded ? '说明' : (ev.data.title_cn || '').slice(0, 12)}
            </button>
            {onDeleteViz && (
              <button
                type="button"
                onClick={() => onDeleteViz(ev.data.id || ev.data.viz_ref)}
                disabled={actionsLocked}
                style={{
                  border: 'none',
                  background: 'none',
                  color: '#b42318',
                  fontSize: 11,
                  cursor: actionsLocked ? 'not-allowed' : 'pointer',
                  opacity: actionsLocked ? 0.6 : 1,
                  padding: '2px 4px',
                }}
                title="删除此可视化"
              >
                ×
              </button>
            )}
          </div>
        ))}
      </div>
      )}
      {active ? (
      <div style={{ padding: fullWidth ? 12 : 8, border: '1px solid #eee', borderRadius: 10 }}>
        <div style={{ fontWeight: 700, marginBottom: 6, fontSize: fullWidth ? 18 : 16 }}>{active.data.title_cn}</div>
        <div style={{ ...mutedStyle, marginBottom: 10, fontSize: 13 }}>
          学习目标: <RichText text={active.data.learning_goal || ''} />
        </div>
        <VizSandbox
          key={active.data.id}
          questionId={questionId}
          vizId={active.data.id}
          engine={active.data.engine}
          executionPayload={active.data.execution_payload}
          params={resolvedParams}
          specJson={active.data.spec_json ?? null}
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
      ) : (
        <div style={{
          padding: 12,
          border: '1px dashed #ddd',
          borderRadius: 10,
          color: '#777',
          fontSize: 13,
        }}>
          已生成可视化规格，正在等待代码生成完成。
        </div>
      )}
    </div>
  );
}

function VisualizationPlanCard({ plan }: { plan: any }) {
  const visualizations = Array.isArray(plan?.visualizations) ? plan.visualizations : [];
  const selected = plan?.selected_visualization ?? null;
  if (!visualizations.length && !selected) return null;
  return (
    <div style={{
      marginBottom: 12,
      padding: 12,
      border: '1px solid #e6ecf3',
      borderRadius: 10,
      background: '#fafcff',
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>可视化规格</div>
      <div style={{ ...mutedStyle, marginBottom: 8 }}>
        Stage 1 已产出 {visualizations.length || (selected ? 1 : 0)} 个关键规格，Stage 2 为这些规格分别生成 GeoGebra 代码。
      </div>
      {selected && (
        <div style={{ marginBottom: 10, lineHeight: 1.6 }}>
          <div><strong>主规格:</strong> <RichText text={selected.title || selected.id || ''} /></div>
          <div><strong>教学目标:</strong> <RichText text={selected.pedagogical_purpose || ''} /></div>
          <div><strong>展示结论:</strong> <RichText text={selected.mathematical_claim_being_shown || ''} /></div>
        </div>
      )}
      {!!visualizations.length && (
        <div style={{ display: 'grid', gap: 8 }}>
          {visualizations.map((item: any, idx: number) => {
            const selectedId = String(selected?.id || plan?.selected_visualization_id || '');
            const isSelected = Boolean(selectedId && item?.id === selectedId);
            const isRecommended = Boolean(item?.recommended);
            const highlighted = isSelected || (!selectedId && isRecommended);
            return (
              <div
                key={item?.id || idx}
                style={{
                  border: `1px solid ${highlighted ? '#d5e4fb' : '#e5e7eb'}`,
                  borderRadius: 8,
                  padding: '8px 10px',
                  background: highlighted ? '#eef5ff' : '#fff',
                }}
              >
                <div style={{ fontWeight: 600 }}>
                  {isSelected ? '已选 · ' : isRecommended ? '推荐 · ' : ''}
                  <RichText text={item?.title || item?.id || `候选 ${idx + 1}`} />
                </div>
                <div style={{ ...mutedStyle, marginTop: 4 }}>
                  <RichText text={item?.pedagogical_purpose || ''} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function deriveVizParams(executionPayload: any, spec: any): VizParam[] {
  const specParams = Array.isArray(spec?.interaction_and_animation?.parameters)
    ? spec.interaction_and_animation.parameters
    : [];
  const byName = new Map<string, any>(specParams.map((param: any) => [String(param?.name || ''), param]));
  const interactionObjects = Array.isArray(executionPayload?.interaction_objects)
    ? executionPayload.interaction_objects
    : [];

  if (interactionObjects.length) {
    return interactionObjects.flatMap((obj: any) => {
      const name = String(obj?.name || '');
      const type = String(obj?.type || '').toLowerCase();
      const param = byName.get(name);
      if (!name || !param) return [];
      if (type === 'checkbox') {
        return [{
          name,
          label_cn: String(param?.meaning || name),
          kind: 'toggle' as const,
          default: parseBooleanValue(param.default_value),
        }];
      }
      if (type !== 'slider') return [];
      const range = parseStructuredRange(param?.range, String(param?.type || '').toLowerCase())
        ?? parseNumericRange(param?.range_or_values, String(param?.type || '').toLowerCase());
      if (!range) return [];
      return [{
        name,
        label_cn: String(param?.meaning || name),
        kind: 'slider' as const,
        min: range.min,
        max: range.max,
        step: range.step,
        default: parseNumericValue(param.default_value, range.min),
      }];
    });
  }

  return specParams.flatMap((param: any) => {
    const type = String(param?.type || '').toLowerCase();
    const label = String(param?.meaning || param?.name || '');
    if (!param?.name) return [];
    if (type === 'boolean') {
      return [{
        name: String(param.name),
        label_cn: label,
        kind: 'toggle' as const,
        default: parseBooleanValue(param.default_value),
      }];
    }
    const range = parseStructuredRange(param?.range, type)
      ?? parseNumericRange(param?.range_or_values, type);
    if (!range) return [];
    return [{
      name: String(param.name),
      label_cn: label,
      kind: 'slider' as const,
      min: range.min,
      max: range.max,
      step: range.step,
      default: parseNumericValue(param.default_value, range.min),
    }];
  });
}

function parseBooleanValue(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  return String(value || '').toLowerCase() === 'true';
}

function parseNumericValue(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseStructuredRange(
  value: unknown,
  type: string,
): { min: number; max: number; step: number } | null {
  if (!value || typeof value !== 'object') return null;
  const range = value as { min?: unknown; max?: unknown; step?: unknown };
  const min = Number(range.min);
  const max = Number(range.max);
  const step = range.step === undefined
    ? (type === 'integer_step' ? 1 : 0.1)
    : Number(range.step);
  if (!Number.isFinite(min) || !Number.isFinite(max) || min >= max) {
    return null;
  }
  return {
    min,
    max,
    step: Number.isFinite(step) && step > 0 ? step : (type === 'integer_step' ? 1 : 0.1),
  };
}

function parseNumericRange(value: unknown, type: string): { min: number; max: number; step: number } | null {
  if (typeof value !== 'string') return null;
  const numbers = value.match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];
  if (numbers.length < 2) return null;
  const explicitStep = value.match(/step\s*[:=]?\s*(-?\d+(?:\.\d+)?)/i);
  const step = explicitStep ? Number(explicitStep[1]) : (type === 'integer_step' ? 1 : 0.1);
  return {
    min: numbers[0],
    max: numbers[1],
    step: Number.isFinite(step) && step > 0 ? step : 0.1,
  };
}

function ToastNotification({
  message,
  kind,
  onDismiss,
}: {
  message: string;
  kind: 'info' | 'success' | 'error';
  onDismiss: () => void;
}) {
  const palette =
    kind === 'success'
      ? { bg: '#eef8f0', fg: '#1f7a3d', border: '#cfe8d5' }
      : kind === 'error'
        ? { bg: '#fff1f1', fg: '#b42318', border: '#f4c7c7' }
        : { bg: '#eef5ff', fg: '#245ea8', border: '#d5e4fb' };
  return (
    <div
      role="alert"
      onClick={onDismiss}
      style={{
        position: 'fixed',
        top: 16,
        left: '50%',
        transform: 'translateX(-50%)',
        padding: '10px 20px',
        borderRadius: 8,
        background: palette.bg,
        color: palette.fg,
        border: `1px solid ${palette.border}`,
        boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
        fontSize: 14,
        fontWeight: 500,
        cursor: 'pointer',
        zIndex: 9999,
        maxWidth: '90vw',
        textAlign: 'center',
      }}
    >
      {kind === 'info' && '⏳ '}
      {kind === 'success' && '✓ '}
      {kind === 'error' && '⚠ '}
      {message}
    </div>
  );
}
