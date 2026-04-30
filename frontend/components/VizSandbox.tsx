'use client';

import GeoGebraSandbox from './GeoGebraSandbox';
import type { VizParam } from './vizCommon';

type Props = {
  questionId?: string;
  vizId: string;
  engine?: string | null;
  executionPayload?: Record<string, unknown> | null;
  params?: VizParam[];
  specJson?: Record<string, unknown> | null;
  height?: number;
};

/** GeoGebra-only visualization host. */
export default function VizSandbox(props: Props) {
  const params = props.params ?? [];
  return (
    <GeoGebraSandbox
      questionId={props.questionId}
      vizId={props.vizId}
      executionPayload={props.executionPayload ?? null}
      params={params}
      specJson={props.specJson ?? null}
      height={props.height}
    />
  );
}
