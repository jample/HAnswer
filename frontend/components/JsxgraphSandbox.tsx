'use client';

import { useEffect, useRef, useState } from 'react';

import { apiUrl } from '../lib/api';
import { ParamControls, type VizParam } from './vizCommon';

type Props = {
  questionId?: string;
  vizId: string;
  jsxCode: string;
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
 * Sandboxed iframe host for one JSXGraph visualization (legacy engine).
 *
 * - Iframe is served from `/viz/sandbox.html` with strict CSP and
 *   `sandbox="allow-scripts"` (no `allow-same-origin`), so the guest
 *   cannot touch the host.
 * - Host <-> guest uses the postMessage protocol from §3.3.2.
 */
export default function JsxgraphSandbox({
  questionId,
  vizId,
  jsxCode,
  params = [],
  specJson = null,
  height = 360,
}: Props) {
  const opaqueSandboxOrigin = 'null';
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [metric, setMetric] = useState<number | null>(null);
  const initialParams = useRef<Record<string, unknown>>(
    Object.fromEntries(params.map((p) => [p.name, p.default])),
  );
  const currentSpec = useRef<Record<string, unknown> | null>(cloneSpec(specJson));
  const logQueueRef = useRef<TraceEvent[]>([]);
  const flushTimerRef = useRef<number | null>(null);
  const flushVisualActionsRef = useRef<() => void>(() => {});
  const enqueueVisualActionRef = useRef<(event: TraceEvent) => void>(() => {});

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
      engine: event.engine ?? 'jsxgraph',
      component: event.component ?? 'JsxgraphSandbox',
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
      if (ev.origin !== opaqueSandboxOrigin) return;
      const msg = (ev.data || {}) as {
        type?: string;
        message?: unknown;
        renderMs?: unknown;
        event?: TraceEvent;
      };
      if (msg.type === 'ready') {
        setReady(true);
        enqueueVisualActionRef.current({ action: 'sandbox.ready', status: 'ok', component: 'sandbox' });
      } else if (msg.type === 'error') {
        const message = String(msg.message || 'viz error');
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
        enqueueVisualActionRef.current({
          action: 'sandbox.metric',
          status: 'ok',
          component: 'sandbox',
          details: { render_ms: renderMs },
        });
      } else if (msg.type === 'trace' && msg.event) {
        enqueueVisualActionRef.current(msg.event);
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  useEffect(() => {
    initialParams.current = Object.fromEntries(params.map((p) => [p.name, p.default]));
  }, [params]);

  useEffect(() => {
    currentSpec.current = cloneSpec(specJson);
  }, [specJson]);

  useEffect(() => {
    enqueueVisualActionRef.current({
      action: 'host.mount',
      status: 'info',
      phase: 'host',
      component: 'JsxgraphSandbox',
    });
    return () => {
      enqueueVisualActionRef.current({
        action: 'host.unmount',
        status: 'info',
        phase: 'host',
        component: 'JsxgraphSandbox',
      });
      flushVisualActionsRef.current();
    };
  }, [questionId, vizId]);

  useEffect(() => {
    setErr(null);
    setMetric(null);
  }, [jsxCode, specJson, vizId]);

  useEffect(() => {
    if (!ready) return;
    // The sandboxed iframe has an opaque origin, so host -> iframe messages
    // must use '*' and be authenticated by the iframe via ev.origin.
    enqueueVisualActionRef.current({
      action: 'host.render.requested',
      status: 'info',
      phase: 'host',
      component: 'JsxgraphSandbox',
      details: {
        code_length: jsxCode.length,
        has_spec: Boolean(specJson),
        parameter_names: Object.keys(initialParams.current),
      },
    });
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'render', jsxCode, params: initialParams.current, spec: currentSpec.current },
      '*',
    );
  }, [ready, jsxCode, specJson]);

  useEffect(() => {
    const win = iframeRef.current?.contentWindow;
    return () => {
      enqueueVisualActionRef.current({
        action: 'host.dispose.requested',
        status: 'info',
        phase: 'host',
        component: 'JsxgraphSandbox',
      });
      win?.postMessage({ type: 'dispose' }, '*');
      flushVisualActionsRef.current();
    };
  }, []);

  function updateParam(name: string, value: unknown) {
    initialParams.current = { ...initialParams.current, [name]: value };
    currentSpec.current = updateSpecParameter(currentSpec.current, name, value);
    enqueueVisualActionRef.current({
      action: 'host.param.updated',
      status: 'info',
      phase: 'host',
      component: 'ParamControls',
      details: { name, value },
    });
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'update-params', params: initialParams.current, spec: currentSpec.current },
      '*',
    );
  }

  return (
    <div style={{ border: '1px solid #ddd', borderRadius: 6, padding: 8 }}>
      {!err ? (
        <>
          <iframe
            ref={iframeRef}
            title={`viz-${vizId}`}
            src="/viz/sandbox.html"
            sandbox="allow-scripts"
            style={{ width: '100%', height, border: 'none', background: '#fff' }}
          />
          <ParamControls params={params} onChange={updateParam} />
        </>
      ) : (
        <VisualizationFallbackCard err={err} specJson={specJson} />
      )}
      {metric !== null && (
        <div style={{ marginTop: 4, fontSize: 11, color: '#999' }}>
          render {metric.toFixed(1)} ms
        </div>
      )}
    </div>
  );
}

function cloneSpec(spec: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!spec) return null;
  return JSON.parse(JSON.stringify(spec)) as Record<string, unknown>;
}

function updateSpecParameter(
  spec: Record<string, unknown> | null,
  name: string,
  value: unknown,
): Record<string, unknown> | null {
  if (!spec) return spec;
  const next = cloneSpec(spec);
  const interaction = next?.interaction_and_animation as { parameters?: Array<Record<string, unknown>> } | undefined;
  if (Array.isArray(interaction?.parameters)) {
    interaction.parameters = interaction.parameters.map((param) => (
      param?.name === name ? { ...param, default_value: value } : param
    ));
  }
  return {
    ...next,
    host_runtime: {
      ...((next as Record<string, unknown>).host_runtime as Record<string, unknown> | undefined),
      parameter_values: {
        ...(((next as Record<string, unknown>).host_runtime as { parameter_values?: Record<string, unknown> } | undefined)?.parameter_values ?? {}),
        [name]: value,
      },
    },
  };
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
  const fallback = asString(implementationGuidance?.fallback_if_animation_is_too_complex);
  const purpose = asString(specJson?.pedagogical_purpose);
  const conclusion = asString(expectedResult?.mathematical_conclusion_visible_to_student);

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
        当前 JSXGraph 代码未通过前端运行，页面先保留教学意图与备用展示方案，避免这一题完全失去可视化解释。
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
