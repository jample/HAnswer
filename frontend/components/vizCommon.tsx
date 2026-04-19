'use client';

import type { CSSProperties, ChangeEvent } from 'react';

export type VizParam = {
  name: string;
  label_cn?: string;
  kind: 'slider' | 'toggle';
  min?: number;
  max?: number;
  step?: number;
  default: unknown;
};

export function ParamControls({
  params,
  onChange,
  style,
}: {
  params: VizParam[];
  onChange: (name: string, value: unknown) => void;
  style?: CSSProperties;
}) {
  if (!params.length) return null;
  return (
    <div style={{ marginTop: 8, display: 'grid', gap: 6, ...style }}>
      {params.map((p) => (
        <label key={p.name} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ minWidth: 80, fontSize: 12, color: '#555' }}>
            {p.label_cn || p.name}
          </span>
          {p.kind === 'toggle' ? (
            <input
              type="checkbox"
              defaultChecked={!!p.default}
              onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(p.name, e.target.checked)}
            />
          ) : (
            <input
              type="range"
              min={p.min}
              max={p.max}
              step={p.step}
              defaultValue={Number(p.default)}
              onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(p.name, Number(e.target.value))}
              style={{ flex: 1 }}
            />
          )}
        </label>
      ))}
    </div>
  );
}
