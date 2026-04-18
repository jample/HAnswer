'use client';

import { useEffect, useState } from 'react';

import { apiUrl } from '../../lib/api';

type ByPrompt = {
  task: string;
  prompt_version: string;
  calls: number;
  cost_usd: number;
  prompt_tokens: number;
  completion_tokens: number;
  avg_latency_ms: number;
};

type ByDay = { date: string; cost_usd: number; calls: number };

type CostSummary = {
  window_days: number;
  totals: {
    cost_usd: number;
    prompt_tokens: number;
    completion_tokens: number;
    calls: number;
    ok: number;
    repaired: number;
    error: number;
  };
  by_prompt: ByPrompt[];
  by_day: ByDay[];
};

type PromptMeta = {
  name: string;
  version: string;
  purpose?: string;
};

type ConfigView = {
  gemini: {
    api_key_masked: string;
    api_key_configured: boolean;
    model_parser: string;
    model_solver: string;
    model_vizcoder: string;
    model_embed: string;
    embed_dim: number;
  };
  postgres: { dsn_masked: string };
  milvus: { host: string; port: number; database: string; auto_bootstrap: boolean };
  retrieval: Record<string, unknown>;
  llm: Record<string, unknown>;
  dialog: Record<string, unknown>;
  server: { host: string; port: number; cors_origins: string[] };
  note: string;
};

type DialogStats = {
  sessions: number;
  question_linked_sessions: number;
  messages: number;
  memory_snapshots: number;
};

const muted = { color: '#888', fontSize: 12 } as const;
const h2Style = { marginTop: 20, borderBottom: '1px solid #eee', paddingBottom: 4 } as const;

export default function SettingsPage() {
  const [days, setDays] = useState(7);
  const [cost, setCost] = useState<CostSummary | null>(null);
  const [prompts, setPrompts] = useState<PromptMeta[]>([]);
  const [cfg, setCfg] = useState<ConfigView | null>(null);
  const [dialogStats, setDialogStats] = useState<DialogStats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(apiUrl(`/api/admin/llm-cost?days=${days}`))
      .then((r) => r.json())
      .then((d) => setCost(d))
      .catch((e) => setErr(String(e)));
  }, [days]);

  useEffect(() => {
    fetch(apiUrl('/api/admin/prompts'))
      .then((r) => r.json())
      .then((d) => setPrompts(d.prompts || []))
      .catch(() => {});
    fetch(apiUrl('/api/admin/config'))
      .then((r) => r.json())
      .then(setCfg)
      .catch(() => {});
    fetch(apiUrl('/api/dialog/stats'))
      .then((r) => r.json())
      .then(setDialogStats)
      .catch(() => {});
  }, []);

  return (
    <section className="page-section">
      <h1>设置</h1>
      <p style={muted}>本地优先部署 · 修改配置请编辑 <code>backend/config.toml</code> 后重启 <code>uvicorn</code>。</p>

      <h2 style={h2Style}>当前配置</h2>
      {cfg ? (
        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
          <Card title="Gemini">
            <Row k="API Key" v={cfg.gemini.api_key_configured
              ? <code>{cfg.gemini.api_key_masked}</code>
              : <span style={{ color: '#c00' }}>未配置</span>} />
            <Row k="Parser 模型" v={<code>{cfg.gemini.model_parser}</code>} />
            <Row k="Solver 模型" v={<code>{cfg.gemini.model_solver}</code>} />
            <Row k="VizCoder 模型" v={<code>{cfg.gemini.model_vizcoder}</code>} />
            <Row k="Embedding 模型" v={<code>{cfg.gemini.model_embed}</code>} />
            <Row k="向量维度" v={cfg.gemini.embed_dim} />
          </Card>
          <Card title="PostgreSQL">
            <Row k="DSN" v={<code style={{ wordBreak: 'break-all' }}>{cfg.postgres.dsn_masked}</code>} />
          </Card>
          <Card title="Milvus">
            <Row k="主机" v={`${cfg.milvus.host}:${cfg.milvus.port}`} />
            <Row k="数据库" v={cfg.milvus.database} />
            <Row k="自动建集合" v={cfg.milvus.auto_bootstrap ? '是' : '否'} />
          </Card>
          <Card title="Retrieval (M5)">
            {Object.entries(cfg.retrieval).map(([k, v]) => (
              <Row key={k} k={k} v={<code>{String(v)}</code>} />
            ))}
          </Card>
          <Card title="LLM 重试">
            {Object.entries(cfg.llm).map(([k, v]) => (
              <Row key={k} k={k} v={<code>{String(v)}</code>} />
            ))}
          </Card>
          <Card title="Dialog Memory">
            {Object.entries(cfg.dialog).map(([k, v]) => (
              <Row key={k} k={k} v={<code>{String(v)}</code>} />
            ))}
          </Card>
        </div>
      ) : (
        <p style={muted}>加载中…</p>
      )}
      {cfg && <p style={{ ...muted, marginTop: 8 }}>{cfg.note}</p>}

      <h2 style={h2Style}>对话分析</h2>
      {dialogStats ? (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <Stat label="会话数" value={dialogStats.sessions} />
          <Stat label="绑定题目会话" value={dialogStats.question_linked_sessions} />
          <Stat label="消息数" value={dialogStats.messages} />
          <Stat label="记忆快照" value={dialogStats.memory_snapshots} />
        </div>
      ) : (
        <p style={muted}>加载中…</p>
      )}

      <h2 style={h2Style}>成本账本</h2>
      <label>
        窗口 (天):
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}
          style={{ marginLeft: 8 }}>
          {[1, 3, 7, 14, 30, 90].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>

      {err && <p style={{ color: '#c00' }}>{err}</p>}

      {cost ? (
        <>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 8 }}>
            <Stat label="总成本 (USD)" value={cost.totals.cost_usd.toFixed(6)} />
            <Stat label="调用数" value={cost.totals.calls} />
            <Stat label="OK" value={cost.totals.ok} />
            <Stat label="Repaired" value={cost.totals.repaired} />
            <Stat label="Error" value={cost.totals.error} />
            <Stat label="输入 tokens" value={cost.totals.prompt_tokens} />
            <Stat label="输出 tokens" value={cost.totals.completion_tokens} />
          </div>

          <h3 style={{ marginTop: 16 }}>按 Prompt / 版本</h3>
          {cost.by_prompt.length === 0 ? (
            <p style={muted}>窗口内无调用记录。</p>
          ) : (
            <div className="table-scroll"><table style={tableStyle}>
              <thead>
                <tr>
                  <th>Prompt</th><th>版本</th><th>调用</th>
                  <th>成本 (USD)</th><th>输入</th><th>输出</th><th>平均延迟 (ms)</th>
                </tr>
              </thead>
              <tbody>
                {cost.by_prompt.map((r, i) => (
                  <tr key={i}>
                    <td>{r.task}</td>
                    <td style={muted}>{r.prompt_version}</td>
                    <td>{r.calls}</td>
                    <td>{r.cost_usd.toFixed(6)}</td>
                    <td>{r.prompt_tokens}</td>
                    <td>{r.completion_tokens}</td>
                    <td>{r.avg_latency_ms}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}

          <h3 style={{ marginTop: 16 }}>按天</h3>
          {cost.by_day.length === 0 ? (
            <p style={muted}>无数据。</p>
          ) : (
            <div className="table-scroll"><table style={tableStyle}>
              <thead><tr><th>日期</th><th>成本 (USD)</th><th>调用</th></tr></thead>
              <tbody>
                {cost.by_day.map((d, i) => (
                  <tr key={i}>
                    <td>{d.date}</td><td>{d.cost_usd.toFixed(6)}</td><td>{d.calls}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </>
      ) : (
        <p style={muted}>加载中…</p>
      )}

      <h2 style={h2Style}>Prompt 注册表</h2>
      {prompts.length === 0 ? (
        <p style={muted}>加载中…</p>
      ) : (
        <table style={tableStyle}>
          <thead><tr><th>名称</th><th>版本</th><th>用途</th></tr></thead>
          <tbody>
            {prompts.map((p, i) => (
              <tr key={i}>
                <td><code>{p.name}</code></td>
                <td style={muted}>{p.version}</td>
                <td>{p.purpose || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div style={{ padding: 8, background: '#f8f8f8', borderRadius: 6, minWidth: 120 }}>
      <div style={muted}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

const tableStyle: React.CSSProperties = {
  borderCollapse: 'collapse',
  marginTop: 8,
  width: '100%',
  maxWidth: 800,
  fontSize: 14,
};

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: 12, border: '1px solid #e0e0e0', borderRadius: 6 }}>
      <strong style={{ display: 'block', marginBottom: 6 }}>{title}</strong>
      {children}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between',
      gap: 8, padding: '2px 0', fontSize: 13 }}>
      <span style={muted}>{k}</span>
      <span>{v}</span>
    </div>
  );
}
