'use client';

import { useEffect, useRef, useState } from 'react';

import { ParamControls, type VizParam } from './vizCommon';

type Props = {
  vizId: string;
  ggbCommands: string[];
  ggbSettings?: Record<string, unknown> | null;
  params?: VizParam[];
  height?: number;
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
  vizId,
  ggbCommands,
  ggbSettings,
  params = [],
  height = 420,
}: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [metric, setMetric] = useState<number | null>(null);
  const renderedRef = useRef(false);
  const liveParams = useRef<Record<string, unknown>>(
    Object.fromEntries(params.map((p) => [p.name, p.default])),
  );

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      if (ev.source !== iframeRef.current?.contentWindow) return;
      const msg = (ev.data || {}) as { type?: string; message?: unknown; renderMs?: unknown };
      if (msg.type === 'ready') setReady(true);
      else if (msg.type === 'error') setErr(String(msg.message || 'GeoGebra 渲染失败'));
      else if (msg.type === 'metric') setMetric(Number(msg.renderMs));
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  // Send the initial render request as soon as the iframe signals ready.
  // The iframe will hold it in pendingRender until the GeoGebra applet
  // actually finishes loading from the CDN, then re-`ready` afterwards.
  useEffect(() => {
    if (!ready || renderedRef.current) return;
    renderedRef.current = true;
    iframeRef.current?.contentWindow?.postMessage(
      {
        type: 'render',
        engine: 'geogebra',
        ggbCommands,
        ggbSettings: ggbSettings ?? null,
        params: liveParams.current,
      },
      '*',
    );
  }, [ready, ggbCommands, ggbSettings]);

  useEffect(() => {
    const win = iframeRef.current?.contentWindow;
    return () => {
      win?.postMessage({ type: 'dispose' }, '*');
    };
  }, []);

  function updateParam(name: string, value: unknown) {
    liveParams.current = { ...liveParams.current, [name]: value };
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'update-params', params: liveParams.current },
      '*',
    );
  }

  return (
    <div style={{ border: '1px solid #ddd', borderRadius: 6, padding: 8 }}>
      <iframe
        ref={iframeRef}
        title={`viz-ggb-${vizId}`}
        src="/viz/geogebra-sandbox.html"
        sandbox="allow-scripts allow-same-origin allow-popups"
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
          GeoGebra render {metric.toFixed(1)} ms
        </div>
      )}
    </div>
  );
}
