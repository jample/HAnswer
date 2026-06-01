'use client';

import { useEffect, useRef, useState } from 'react';

import { apiUrl } from '../lib/api';
import { ParamControls, type VizParam } from './vizCommon';

type Props = {
  questionId?: string;
  vizId: string;
  executionPayload?: Record<string, unknown> | null;
  params?: VizParam[];
  specJson?: Record<string, unknown> | null;
  height?: number;
};

type TraceEvent = {
  timestamp?: string;
  source?: string;
  phase?: string;
  action?: string;
  status?: string;
  question_id?: string;
  visualization_id?: string;
  engine?: string;
  component?: string;
  details?: Record<string, unknown>;
  error?: string;
};

/**
 * Sandboxed iframe host for one GeoGebra visualization.
 *
 * The iframe is served from `/viz/geogebra-sandbox.html`, loads the
 * GeoGebra Apps API from the official CDN, and exposes the same
 * postMessage protocol as the JSXGraph sandbox so the host
 * doesn't care which engine renders.
 *
 * The GeoGebra sandbox needs `allow-same-origin` because deployggb's
 * loader fetches GWT chunks via XHR. Safe because the LLM never emits
 * JS here — only GeoGebra command strings interpreted by the runtime.
 */
export default function GeoGebraSandbox({
  questionId,
  vizId,
  executionPayload = null,
  params = [],
  specJson = null,
  height = 420,
}: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [metric, setMetric] = useState<number | null>(null);
  const [partialMessage, setPartialMessage] = useState<string | null>(null);
  const renderedRef = useRef(false);
  const logQueueRef = useRef<TraceEvent[]>([]);
  const flushTimerRef = useRef<number | null>(null);
  const flushVisualActionsRef = useRef<() => void>(() => {});
  const enqueueVisualActionRef = useRef<(event: TraceEvent) => void>(() => {});
  const liveParams = useRef<Record<string, unknown>>(
    Object.fromEntries(params.map((p) => [p.name, p.default])),
  );

  flushVisualActionsRef.current = () => {
    if (!logQueueRef.current.length) return;
    const records = logQueueRef.current.splice(0, logQueueRef.current.length);
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    void fetch(apiUrl('/api/answer/visual-actions'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ records }),
      keepalive: true,
    }).catch(() => {});
  };

  enqueueVisualActionRef.current = (event: TraceEvent) => {
    logQueueRef.current.push({
      timestamp: event.timestamp ?? new Date().toISOString(),
      source: event.source ?? 'frontend',
      phase: event.phase ?? 'runtime',
      action: event.action ?? 'unknown',
      status: event.status ?? 'info',
      question_id: event.question_id ?? questionId,
      visualization_id: event.visualization_id ?? vizId,
      engine: event.engine ?? 'geogebra',
      component: event.component ?? 'GeoGebraSandbox',
      details: event.details ?? {},
      error: event.error,
    });
    if (logQueueRef.current.length >= 10) {
      flushVisualActionsRef.current();
      return;
    }
    if (flushTimerRef.current !== null) return;
    flushTimerRef.current = window.setTimeout(() => {
      flushTimerRef.current = null;
      flushVisualActionsRef.current();
    }, 250);
  };

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      if (ev.source !== iframeRef.current?.contentWindow) return;
      const msg = (ev.data || {}) as {
        type?: string;
        message?: unknown;
        renderMs?: unknown;
        details?: Record<string, unknown>;
        event?: TraceEvent;
      };
      if (msg.type === 'ready') {
        setReady(true);
        enqueueVisualActionRef.current({ action: 'sandbox.ready', status: 'ok', component: 'sandbox' });
      } else if (msg.type === 'error') {
        const message = String(msg.message || 'GeoGebra 渲染失败');
        setErr(message);
        enqueueVisualActionRef.current({
          action: 'sandbox.error',
          status: 'error',
          component: 'sandbox',
          error: message,
        });
      } else if (msg.type === 'metric') {
        const renderMs = Number(msg.renderMs);
        setMetric(renderMs);
        if (Number(msg.details?.partial_failure_count ?? 0) > 0) {
          setPartialMessage('部分辅助元素未显示');
        }
        enqueueVisualActionRef.current({
          action: 'sandbox.metric',
          status: 'ok',
          component: 'sandbox',
          details: { render_ms: renderMs, ...(msg.details ?? {}) },
        });
      } else if (msg.type === 'trace' && msg.event) {
        if (msg.event.action === 'runtime.partial_passed') {
          setPartialMessage('部分辅助元素未显示');
        }
        enqueueVisualActionRef.current(msg.event);
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  useEffect(() => {
    enqueueVisualActionRef.current({
      action: 'host.mount',
      status: 'info',
      phase: 'host',
      component: 'GeoGebraSandbox',
    });
    return () => {
      enqueueVisualActionRef.current({
        action: 'host.unmount',
        status: 'info',
        phase: 'host',
        component: 'GeoGebraSandbox',
      });
      flushVisualActionsRef.current();
    };
  }, [questionId, vizId]);

  useEffect(() => {
    setErr(null);
    setMetric(null);
    setPartialMessage(null);
    renderedRef.current = false;
  }, [executionPayload, specJson, vizId]);

  // Send the initial render request as soon as the iframe signals ready.
  // The iframe will hold it in pendingRender until the GeoGebra applet
  // actually finishes loading from the CDN, then re-`ready` afterwards.
  useEffect(() => {
    if (!ready || renderedRef.current) return;
    const commands = Array.isArray(executionPayload?.commands) ? executionPayload.commands : [];
    if (!executionPayload || !commands.length) {
      const message = '当前 GeoGebra 指令为空，已回退为规格说明卡片';
      setErr(message);
      enqueueVisualActionRef.current({
        action: 'host.render.skipped',
        status: 'degraded',
        phase: 'host',
        component: 'GeoGebraSandbox',
        details: { has_spec: Boolean(specJson), command_count: 0, has_execution_payload: Boolean(executionPayload) },
        error: message,
      });
      return;
    }
    renderedRef.current = true;
    enqueueVisualActionRef.current({
      action: 'host.render.requested',
      status: 'info',
      phase: 'host',
      component: 'GeoGebraSandbox',
      details: {
        command_count: commands.length,
        property_command_count: Array.isArray(executionPayload.property_commands)
          ? executionPayload.property_commands.length
          : 0,
        has_spec: Boolean(specJson),
        parameter_names: Object.keys(liveParams.current),
      },
    });
    iframeRef.current?.contentWindow?.postMessage(
      {
        type: 'render',
        executionPayload,
        spec: specJson ?? null,
        params: liveParams.current,
      },
      '*',
    );
  }, [ready, executionPayload, specJson]);

  useEffect(() => {
    const win = iframeRef.current?.contentWindow;
    return () => {
      enqueueVisualActionRef.current({
        action: 'host.dispose.requested',
        status: 'info',
        phase: 'host',
        component: 'GeoGebraSandbox',
      });
      win?.postMessage({ type: 'dispose' }, '*');
      flushVisualActionsRef.current();
    };
  }, []);

  function updateParam(name: string, value: unknown) {
    liveParams.current = { ...liveParams.current, [name]: value };
    enqueueVisualActionRef.current({
      action: 'host.param.updated',
      status: 'info',
      phase: 'host',
      component: 'ParamControls',
      details: { name, value },
    });
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'update-params', params: liveParams.current },
      '*',
    );
  }

  return (
    <div style={{ border: '1px solid #ddd', borderRadius: 6, padding: 8 }}>
      {!err ? (
        <>
          <iframe
            ref={iframeRef}
            title={`viz-ggb-${vizId}`}
            src="/viz/geogebra-sandbox.html"
            sandbox="allow-scripts allow-same-origin allow-popups"
            style={{ width: '100%', height, border: 'none', background: '#fff' }}
          />
          <ParamControls params={params} onChange={updateParam} />
          {partialMessage && (
            <div style={{ marginTop: 6, fontSize: 12, color: '#9a6700' }}>
              {partialMessage}
            </div>
          )}
        </>
      ) : (
        <VisualizationFallbackCard err={err} specJson={specJson} />
      )}
      {metric !== null && (
        <div style={{ marginTop: 4, fontSize: 11, color: '#999' }}>
          GeoGebra render {metric.toFixed(1)} ms
        </div>
      )}
    </div>
  );
}

function VisualizationFallbackCard({
  err,
  specJson,
}: {
  err: string;
  specJson: Record<string, unknown> | null;
}) {
  const implementationGuidance = asRecord(specJson?.implementation_guidance);
  const expectedResult = asRecord(specJson?.expected_result);
  const geometryContract = asRecord(specJson?.geometry_contract);
  const motion = asRecord(geometryContract?.motion);
  const fallback = asString(implementationGuidance?.fallback_if_animation_is_too_complex);
  const purpose = asString(specJson?.pedagogical_purpose);
  const conclusion = asString(expectedResult?.mathematical_conclusion_visible_to_student);
  const coreObjects = asArray(geometryContract?.core_objects)
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .slice(0, 5);
  const invariants = asArray(geometryContract?.invariants)
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .slice(0, 3);
  const checkpoints = asArray(geometryContract?.student_checkpoints)
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .slice(0, 3);

  return (
    <div
      style={{
        minHeight: 220,
        border: '1px dashed #d0d5dd',
        borderRadius: 10,
        background: '#fcfcfd',
        padding: 16,
        display: 'grid',
        gap: 10,
        alignContent: 'start',
      }}
    >
      <div style={{ fontWeight: 700, color: '#b42318' }}>可视化运行失败，已回退为规格说明卡片</div>
      <div style={{ fontSize: 13, color: '#475467', lineHeight: 1.6 }}>
        当前 GeoGebra 指令未通过前端运行，页面先保留教学意图与备用展示方案，避免这一题完全失去可视化解释。
      </div>
      {purpose && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <strong>教学目标:</strong> {purpose}
        </div>
      )}
      {conclusion && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <strong>应展示的结论:</strong> {conclusion}
        </div>
      )}
      {coreObjects.length > 0 && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <strong>关键对象:</strong>{' '}
          {coreObjects.map((item) => {
            const name = asString(item.name);
            const role = asString(item.role);
            return role ? `${name}（${role}）` : name;
          }).filter(Boolean).join('、')}
        </div>
      )}
      {(asString(motion?.moving_object) || asString(motion?.path_definition)) && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <strong>预期轨迹:</strong>{' '}
          {asString(motion?.moving_object) && `${asString(motion?.moving_object)} `}
          {asString(motion?.path_definition) || asString(motion?.expected_positions_description)}
        </div>
      )}
      {invariants.length > 0 && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <strong>保持不变:</strong>{' '}
          {invariants.map((item) => asString(item.description)).filter(Boolean).join('；')}
        </div>
      )}
      {checkpoints.length > 0 && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <strong>观察顺序:</strong>{' '}
          {checkpoints.map((item) => asString(item.observation)).filter(Boolean).join(' -> ')}
        </div>
      )}
      {fallback && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <strong>规格中的回退方案:</strong> {fallback}
        </div>
      )}
      <div style={{ fontSize: 12, color: '#b42318', lineHeight: 1.5 }}>
        运行错误: {err}
      </div>
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null;
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
