'use client';

import GeoGebraSandbox from './GeoGebraSandbox';
import JsxgraphSandbox from './JsxgraphSandbox';
import type { VizParam } from './vizCommon';

type Props = {
  vizId: string;
  engine?: string | null;
  jsxCode?: string | null;
  ggbCommands?: string[] | null;
  ggbSettings?: Record<string, unknown> | null;
  params?: VizParam[];
  height?: number;
};

/**
 * Dispatcher for visualization rendering.
 *
 * Picks the runtime based on the persisted ``engine`` discriminator:
 * - "geogebra"  → GeoGebraSandbox (preferred, math-professional)
 * - "jsxgraph"  → JsxgraphSandbox (legacy fallback)
 *
 * Older payloads without an explicit engine default to "jsxgraph"
 * for backward compatibility with already-confirmed visualizations.
 */
export default function VizSandbox(props: Props) {
  const engine = (props.engine || 'jsxgraph').toLowerCase();
  const params = props.params ?? [];
  if (engine === 'geogebra') {
    return (
      <GeoGebraSandbox
        vizId={props.vizId}
        ggbCommands={props.ggbCommands ?? []}
        ggbSettings={props.ggbSettings ?? null}
        params={params}
        height={props.height}
      />
    );
  }
  return (
    <JsxgraphSandbox
      vizId={props.vizId}
      jsxCode={props.jsxCode ?? ''}
      params={params}
      height={props.height}
    />
  );
}
