'use client';

import { useEffect, useRef, useState } from 'react';

type Props = {
  vizId: string;
  jsxCode: string;
  params?: { name: string; default: unknown }[];
  height?: number;
};

/**
 * Sandboxed iframe host for one visualization (§3.3).
 *
 * - Iframe is served from `/viz/sandbox.html` with strict CSP
 *   (declared in `next.config.js`) and `sandbox="allow-scripts"`
 *   WITHOUT `allow-same-origin`, so the guest cannot touch the host.
 * - Host ⟷ guest communication uses the postMessage protocol from
 *   HAnswerR.md §3.3.2: init/render/update-params/dispose/ready/error/metric.
 */
export default function VizSandbox({ vizId, jsxCode, params = [], height = 360 }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [metric, setMetric] = useState<number | null>(null);
  const initialParams = useRef<Record<string, unknown>>(
    Object.fromEntries(params.map((p) => [p.name, p.default])),
  );

  // Message loop.
  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      // `ev.source === iframeRef.current?.contentWindow` narrows us to
      // messages from our own guest (sandboxed origin is opaque).
      if (ev.source !== iframeRef.current?.contentWindow) return;
      const msg = ev.data || {};
      if (msg.type === 'ready') setReady(true);
      else if (msg.type === 'error') setErr(String(msg.message || 'viz error'));
      else if (msg.type === 'metric') setMetric(Number(msg.renderMs));
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  // First render when the iframe signals ready.
  useEffect(() => {
    if (!ready) return;
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'render', jsxCode, params: initialParams.current },
      '*',
    );
  }, [ready, jsxCode]);

  useEffect(() => {
    return () => {
      iframeRef.current?.contentWindow?.postMessage({ type: 'dispose' }, '*');
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
      {params.length > 0 && (
        <div style={{ marginTop: 8, display: 'grid', gap: 6 }}>
          {params.map((p: any) => (
            <label key={p.name} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ minWidth: 80, fontSize: 12, color: '#555' }}>
                {p.label_cn || p.name}
              </span>
              {p.kind === 'toggle' ? (
                <input
                  type="checkbox"
                  defaultChecked={!!p.default}
                  onChange={(e) => updateParam(p.name, e.target.checked)}
                />
              ) : (
                <input
                  type="range"
                  min={p.min} max={p.max} step={p.step}
                  defaultValue={Number(p.default)}
                  onChange={(e) => updateParam(p.name, Number(e.target.value))}
                  style={{ flex: 1 }}
                />
              )}
            </label>
          ))}
        </div>
      )}
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
