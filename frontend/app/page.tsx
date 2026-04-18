'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';

type Parsed = {
  subject: 'math' | 'physics';
  grade_band: 'junior' | 'senior';
  topic_path: string[];
  question_text: string;
  given: string[];
  find: string[];
  diagram_description: string;
  difficulty: number;
  tags: string[];
  confidence: number;
};

type IngestResponse = {
  question_id: string;
  parsed: Parsed;
  image_sha256: string;
  deduped: boolean;
};

const box = { border: '1px solid #ddd', borderRadius: 6, padding: 12, marginTop: 12 };
const label = { display: 'block', fontSize: 12, color: '#666', marginBottom: 4 };

export default function AskPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [subject, setSubject] = useState<'' | 'math' | 'physics'>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [qid, setQid] = useState<string | null>(null);
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [deduped, setDeduped] = useState(false);

  async function upload() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      if (subject) fd.append('subject_hint', subject);
      const res = await fetch('/api/ingest/image', { method: 'POST', body: fd });
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body: IngestResponse = await res.json();
      setQid(body.question_id);
      setParsed(body.parsed);
      setDeduped(body.deduped);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function savePatch() {
    if (!qid || !parsed) return;
    setLoading(true);
    try {
      const res = await fetch(`/api/ingest/${qid}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(parsed),
      });
      if (!res.ok) setError(`${res.status} ${await res.text()}`);
    } finally {
      setLoading(false);
    }
  }

  async function startAnswer() {
    if (!qid || !parsed) return;
    await savePatch();
    router.push(`/q/${qid}`);
  }

  const lowConf = parsed && parsed.confidence < 0.5;

  return (
    <section>
      <h1>提问</h1>
      <p style={{ color: '#666' }}>拍照或选择题目图片。上传后可校对解析结果再开始解答。</p>

      <div style={box}>
        <span style={label}>题目图片</span>
        <input
          type="file"
          accept="image/*"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <span style={{ ...label, marginTop: 12 }}>学科 (可选提示)</span>
        <select value={subject} onChange={(e) => setSubject(e.target.value as any)}>
          <option value="">自动判断</option>
          <option value="math">数学</option>
          <option value="physics">物理</option>
        </select>
        <div style={{ marginTop: 12 }}>
          <button onClick={upload} disabled={!file || loading}>
            {loading ? '解析中…' : '上传并解析'}
          </button>
        </div>
        {error && <pre style={{ color: '#b00020', whiteSpace: 'pre-wrap' }}>{error}</pre>}
      </div>

      {parsed && (
        <div style={box}>
          <h2 style={{ marginTop: 0 }}>解析结果 (可校对)</h2>
          {deduped && (
            <p style={{ background: '#fffae6', padding: 8 }}>
              已识别为先前上传过的同一道题目，已更新 seen_count。
            </p>
          )}
          {lowConf && (
            <p style={{ background: '#ffecec', padding: 8, color: '#b00020' }}>
              ⚠️ LLM 置信度较低 ({parsed.confidence.toFixed(2)})，请仔细校对下面字段。
            </p>
          )}

          <ParsedEditor value={parsed} onChange={setParsed} />

          <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
            <button onClick={savePatch} disabled={loading}>保存修改</button>
            <button onClick={startAnswer} disabled={loading}>开始解答 →</button>
          </div>
        </div>
      )}
    </section>
  );
}

function ParsedEditor({ value, onChange }: {
  value: Parsed;
  onChange: (p: Parsed) => void;
}) {
  const set = <K extends keyof Parsed>(k: K, v: Parsed[K]) => onChange({ ...value, [k]: v });

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <Row label="学科">
        <select value={value.subject} onChange={(e) => set('subject', e.target.value as any)}>
          <option value="math">数学</option>
          <option value="physics">物理</option>
        </select>
      </Row>
      <Row label="学段">
        <select value={value.grade_band} onChange={(e) => set('grade_band', e.target.value as any)}>
          <option value="junior">初中</option>
          <option value="senior">高中</option>
        </select>
      </Row>
      <Row label="难度 (1-5)">
        <input
          type="number" min={1} max={5} value={value.difficulty}
          onChange={(e) => set('difficulty', Number(e.target.value))}
          style={{ width: 60 }}
        />
      </Row>
      <Row label="题干 (LaTeX)">
        <textarea
          value={value.question_text}
          rows={4}
          onChange={(e) => set('question_text', e.target.value)}
          style={{ width: '100%' }}
        />
      </Row>
      <Row label="已知 (每行一个)">
        <textarea
          value={value.given.join('\n')}
          rows={3}
          onChange={(e) => set('given', e.target.value.split('\n').filter(Boolean))}
          style={{ width: '100%' }}
        />
      </Row>
      <Row label="求解 (每行一个)">
        <textarea
          value={value.find.join('\n')}
          rows={2}
          onChange={(e) => set('find', e.target.value.split('\n').filter(Boolean))}
          style={{ width: '100%' }}
        />
      </Row>
      <Row label="图形描述">
        <textarea
          value={value.diagram_description}
          rows={2}
          onChange={(e) => set('diagram_description', e.target.value)}
          style={{ width: '100%' }}
        />
      </Row>
      <Row label="知识点路径 (以 > 分隔)">
        <input
          value={value.topic_path.join(' > ')}
          onChange={(e) =>
            set('topic_path', e.target.value.split('>').map((s) => s.trim()).filter(Boolean))
          }
          style={{ width: '100%' }}
        />
      </Row>
      <Row label="标签 (逗号分隔)">
        <input
          value={value.tags.join(', ')}
          onChange={(e) =>
            set('tags', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))
          }
          style={{ width: '100%' }}
        />
      </Row>
      <Row label="LLM 置信度">
        <span>{value.confidence.toFixed(2)}</span>
      </Row>
    </div>
  );
}

function Row({ label: text, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={label}>{text}</div>
      {children}
    </div>
  );
}
