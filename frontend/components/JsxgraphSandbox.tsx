'use client';

import { useEffect, useRef, useState } from 'react';

import { ParamControls, type VizParam } from './vizCommon';

type Props = {
  vizId: string;
  jsxCode: string;
  params?: VizParam[];
  height?: number;
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
  vizId,
  jsxCode,
  params = [],
  height = 360,
}: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [metric, setMetric] = useState<number | null>(null);
  const initialParams = useRef<Record<string, unknown>>(
    Object.fromEntries(params.map((p) => [p.name, p.default])),
  );

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      if (ev.source !== iframeRef.current?.contentWindow) return;
      const msg = (ev.data || {}) as { type?: string; message?: unknown; renderMs?: unknown };
      if (msg.type === 'ready') setReady(true);
      else if (msg.type === 'error') setErr(String(msg.message || 'viz error'));
      else if (msg.type === 'metric') setMetric(Number(msg.renderMs));
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  useEffect(() => {
    if (!ready) return;
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'render', jsxCode, params: initialParams.current },
      '*',
    );
  }, [ready, jsxCode]);

  useEffect(() => {
    const win = iframeRef.current?.contentWindow;
    return () => {
      win?.postMessage({ type: 'dispose' }, '*');
    };
  }, []);

  function updateParam(name: string, value: unknown) {
    initialParams.current = { ...initialParams.current, [name]: value };
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'update-params', params: initialParams.current },
      '*',
    );
  }

  return (
    <div style={{ border: '1px solid #ddd', borderRadius: 6, padding: 8 }}>
      <iframe
        ref={iframeRef}
        title={`viz-${vizId}`}
        src="/viz/sandbox.html"
        sandbox="allow-scripts"
        style={{ width: '100%', height, border: 'none', background: '#fff' }}
      />
      <ParamControls params={params} onChange={updateParam} />
      {err && (
        <div style={{ marginTop: 8, color: '#b00020', fontSize: 12 }}>
          可视化失败: {err}
        </div>
      )}
      {metric !== null && (
        <div style={{ marginTop: 4, fontSize: 11, color: '#999' }}>
          render {metric.toFixed(1)} ms
        </div>
      )}
    </div>
  );
}
