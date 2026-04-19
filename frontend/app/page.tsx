'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

import { RichText } from '../components/MathText';
import { apiUrl } from '../lib/api';

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

const subjectLabel: Record<string, string> = { math: '数学', physics: '物理' };
const gradeLabel: Record<string, string> = { junior: '初中', senior: '高中' };

function confClass(c: number) {
  if (c >= 0.8) return 'good';
  if (c >= 0.5) return 'ok';
  return 'low';
}

export default function AskPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [subject, setSubject] = useState<'' | 'math' | 'physics'>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [qid, setQid] = useState<string | null>(null);
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [deduped, setDeduped] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [localImageUrl, setLocalImageUrl] = useState<string | null>(null);
  const [replacementFile, setReplacementFile] = useState<File | null>(null);
  const [replacementImageUrl, setReplacementImageUrl] = useState<string | null>(null);
  const [recentUploads, setRecentUploads] = useState<{ id: string; text: string }[]>([]);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem('hanswer.recent_uploads');
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) setRecentUploads(arr.slice(0, 10));
      }
    } catch { /* noop */ }
  }, []);

  useEffect(() => {
    if (!file) {
      setLocalImageUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setLocalImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    if (!replacementFile) {
      setReplacementImageUrl(null);
      return;
    }
    const url = URL.createObjectURL(replacementFile);
    setReplacementImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [replacementFile]);

  const pickFile = useCallback((f: File | null | undefined) => {
    if (!f || !f.type.startsWith('image/')) return;
    setFile(f);
    setQid(null);
    setParsed(null);
    setDeduped(false);
    setError(null);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      pickFile(e.dataTransfer.files[0]);
    },
    [pickFile],
  );

  async function upload() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      if (subject) fd.append('subject_hint', subject);
      const res = await fetch(apiUrl('/api/ingest/image'), { method: 'POST', body: fd });
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body: IngestResponse = await res.json();
      setQid(body.question_id);
      setParsed(body.parsed);
      setDeduped(body.deduped);
      // Save to recent uploads
      try {
        const entry = { id: body.question_id, text: body.parsed.question_text?.slice(0, 40) || '(无文本)' };
        const prev = recentUploads.filter((r) => r.id !== body.question_id);
        const next = [entry, ...prev].slice(0, 10);
        setRecentUploads(next);
        window.localStorage.setItem('hanswer.recent_uploads', JSON.stringify(next));
      } catch { /* noop */ }
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
      const res = await fetch(apiUrl(`/api/ingest/${qid}`), {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(parsed),
      });
      if (!res.ok) setError(`${res.status} ${await res.text()}`);
    } finally {
      setLoading(false);
    }
  }

  async function rescan() {
    if (!qid) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(apiUrl(`/api/ingest/${qid}/rescan`), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ subject_hint: subject || null }),
      });
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body: IngestResponse = await res.json();
      setParsed(body.parsed);
      setDeduped(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function replaceImage() {
    if (!qid || !replacementFile) return;
    setLoading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', replacementFile);
      if (subject) fd.append('subject_hint', subject);
      const res = await fetch(apiUrl(`/api/ingest/${qid}/replace-image`), {
        method: 'POST',
        body: fd,
      });
      if (!res.ok) {
        setError(`${res.status} ${await res.text()}`);
        return;
      }
      const body: IngestResponse = await res.json();
      setParsed(body.parsed);
      setDeduped(false);
      setReplacementFile(null);
      setFile(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function startAnswer() {
    if (!qid || !parsed) return;
    await savePatch();
    router.push(`/q/${qid}`);
  }

  const comparisonImageUrl = localImageUrl ?? (qid ? apiUrl(`/api/ingest/${qid}/image`) : null);

  return (
    <div className="ask-page">
      {/* ── Page title ─────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: '0 0 6px', fontSize: 26, fontWeight: 700, letterSpacing: '-0.4px' }}>
          提问
        </h1>
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: 14 }}>
          拍照或选择题目图片，AI 将自动解析题目内容，解析后可校对再开始解答。
        </p>
      </div>

      {/* ── Upload card ────────────────────────────────────────────── */}
      <div className="card">
        <GeminiCallPreview
          loading={loading}
          parsedReady={Boolean(parsed)}
        />
        <label
          className={`upload-zone${dragging ? ' dragging' : ''}${file ? ' has-file' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
          {file ? (
            <>
              <span style={{ fontSize: 36 }}>🖼️</span>
              <span className="upload-filename">{file.name}</span>
              <span className="upload-hint">点击重新选择</span>
            </>
          ) : (
            <>
              <span className="upload-icon">📷</span>
              <span className="upload-text">拖拽图片至此，或点击选择</span>
              <span className="upload-hint">支持 JPG · PNG · HEIC · WEBP</span>
            </>
          )}
        </label>

        {/* Camera capture button for mobile */}
        <label style={{ display: 'inline-block', marginTop: 8, cursor: 'pointer' }}>
          <input
            type="file"
            accept="image/*"
            capture="environment"
            style={{ display: 'none' }}
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
          <span style={{ padding: '6px 16px', background: '#f0f0f0', borderRadius: 4, fontSize: 14 }}>
            📸 拍照上传
          </span>
        </label>

        {/* Recent uploads strip */}
        {recentUploads.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <span style={{ fontSize: 12, color: '#888' }}>最近上传</span>
            <div style={{ display: 'flex', gap: 8, overflowX: 'auto', marginTop: 4 }}>
              {recentUploads.map((r) => (
                <a
                  key={r.id}
                  href={`/q/${r.id}`}
                  style={{
                    flexShrink: 0,
                    padding: '4px 10px',
                    background: '#f8f8f8',
                    border: '1px solid #e0e0e0',
                    borderRadius: 4,
                    fontSize: 12,
                    color: '#0366d6',
                    textDecoration: 'none',
                    whiteSpace: 'nowrap',
                  }}
                >
                  #{r.id.slice(0, 8)} {r.text}
                </a>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, marginTop: 16, flexWrap: 'wrap' }}>
          <div style={{ flex: '0 0 auto' }}>
            <span className="field-label">学科提示</span>
            <select
              value={subject}
              onChange={(e) => setSubject(e.target.value as any)}
              style={{ width: 140 }}
            >
              <option value="">自动判断</option>
              <option value="math">数学</option>
              <option value="physics">物理</option>
            </select>
          </div>
          <button className="btn btn-primary" onClick={upload} disabled={!file || loading}>
            {loading ? <><span className="spinner" />解析中…</> : '上传并解析'}
          </button>
        </div>

        {error && (
          <div className="banner banner-danger" style={{ marginTop: 14 }}>
            <span>⚠️</span>
            <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{error}</span>
          </div>
        )}
      </div>

      {/* ── Results card ───────────────────────────────────────────── */}
      {parsed && (
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>解析结果</h2>
            <span className={`conf-chip ${confClass(parsed.confidence)}`}>
              置信度 {(parsed.confidence * 100).toFixed(0)}%
            </span>
          </div>

          {deduped && (
            <div className="banner banner-warning">
              <span>ℹ️</span>
              <span>已识别为先前上传过的同一道题目，已更新 seen_count。</span>
            </div>
          )}
          {parsed.confidence < 0.5 && (
            <div className="banner banner-danger">
              <span>⚠️</span>
              <span>LLM 置信度较低，请仔细检查并校对下方所有字段。</span>
            </div>
          )}

          <div className="result-compare-grid">
            {comparisonImageUrl && (
              <div className="source-image-card">
                <div className="math-preview-header">
                  <span className="preview-badge">原图对照</span>
                  <span className="preview-subject-badge">上传原始题面</span>
                </div>
                <img src={comparisonImageUrl} alt="题目原图" className="source-image" />
              </div>
            )}

            {/* Math preview */}
            <div className="math-preview">
              <div className="math-preview-header">
                <span className="preview-badge">MathJax 预览</span>
                <span className="preview-subject-badge">
                  {subjectLabel[parsed.subject] ?? parsed.subject}
                  {' · '}
                  {gradeLabel[parsed.grade_band] ?? parsed.grade_band}
                  {' · 难度 '}
                  {parsed.difficulty}
                </span>
              </div>
              <div className="preview-question">
                <RichText text={parsed.question_text} />
              </div>
              {parsed.given.length > 0 && (
                <div className="preview-section">
                  <span className="preview-label">已知</span>
                  <ul className="preview-list">
                    {parsed.given.map((g, i) => (
                      <li key={i}><RichText text={g} /></li>
                    ))}
                  </ul>
                </div>
              )}
              {parsed.find.length > 0 && (
                <div className="preview-section">
                  <span className="preview-label">求</span>
                  <ul className="preview-list">
                    {parsed.find.map((f, i) => (
                      <li key={i}><RichText text={f} /></li>
                    ))}
                  </ul>
                </div>
              )}
              {parsed.diagram_description.trim() && (
                <div className="preview-section">
                  <span className="preview-label">图形描述</span>
                  <div className="math-live-preview math-live-preview-compact">
                    <RichText text={parsed.diagram_description} />
                  </div>
                </div>
              )}
            </div>
          </div>

          <ParsedEditor value={parsed} onChange={setParsed} />

          <div className="card-actions">
            <div className="replace-image-inline">
              <label className="btn btn-secondary replace-image-label">
                选择新原图
                <input
                  type="file"
                  accept="image/*"
                  style={{ display: 'none' }}
                  onChange={(e) => setReplacementFile(e.target.files?.[0] ?? null)}
                />
              </label>
              {replacementFile && (
                <span className="replace-image-name">{replacementFile.name}</span>
              )}
              <button
                className="btn btn-secondary"
                onClick={replaceImage}
                disabled={loading || !replacementFile}
              >
                替换原图并重解析
              </button>
            </div>
            <button className="btn btn-secondary" onClick={rescan} disabled={loading}>
              重新解析原图
            </button>
            <button className="btn btn-secondary" onClick={savePatch} disabled={loading}>
              保存修改
            </button>
            <button className="btn btn-primary" onClick={startAnswer} disabled={loading}>
              {loading ? <><span className="spinner" />处理中…</> : '开始解答 →'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function GeminiCallPreview({
  loading,
  parsedReady,
}: {
  loading: boolean;
  parsedReady: boolean;
}) {
  const stages = [
    {
      idx: 1,
      label: '解析题面',
      desc: 'Gemini Parser 识别题图并抽取结构化题面。',
      state: loading ? 'active' : parsedReady ? 'done' : 'pending',
    },
    {
      idx: 2,
      label: '生成解答',
      desc: 'Gemini Solver 生成教学型解答。',
      state: 'pending',
    },
    {
      idx: 3,
      label: '生成可视化',
      desc: 'Gemini VizCoder 生成交互图形。',
      state: 'pending',
    },
    {
      idx: 4,
      label: '建立索引',
      desc: 'Gemini Embedding 建立检索向量。',
      state: 'pending',
    },
  ] as const;

  return (
    <div style={{ marginBottom: 14, padding: 12, border: '1px solid #e7edf5', borderRadius: 8, background: '#fafcff' }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>Gemini 处理流程</div>
      <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 8 }}>
        当前任务共 4 次 Gemini 调用。上传页完成 1/4，开始解答后将在答题页继续 2/4 到 4/4。
      </div>
      <div style={{ display: 'grid', gap: 8 }}>
        {stages.map((stage) => {
          const palette =
            stage.state === 'done'
              ? { bg: '#eef8f0', fg: '#1f7a3d', border: '#cfe8d5' }
              : stage.state === 'active'
                ? { bg: '#eef5ff', fg: '#245ea8', border: '#d5e4fb' }
                : { bg: '#fff', fg: '#666', border: '#e5e7eb' };
          return (
            <div
              key={stage.idx}
              style={{
                border: `1px solid ${palette.border}`,
                borderRadius: 8,
                padding: '8px 10px',
                background: palette.bg,
                color: palette.fg,
              }}
            >
              <div style={{ fontWeight: 600 }}>
                {stage.state === 'done' ? '✓ ' : stage.state === 'active' ? '● ' : '○ '}
                Gemini {stage.idx}/4 · {stage.label}
              </div>
              <div style={{ fontSize: 12, marginTop: 2 }}>{stage.desc}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── ParsedEditor ──────────────────────────────────────────────────── */

function ParsedEditor({
  value,
  onChange,
}: {
  value: Parsed;
  onChange: (p: Parsed) => void;
}) {
  const set = <K extends keyof Parsed>(k: K, v: Parsed[K]) => onChange({ ...value, [k]: v });

  return (
    <div className="editor-grid">
      <div className="editor-section-title">基本信息</div>
      <div className="editor-row editor-row-3">
        <Field label="学科">
          <select value={value.subject} onChange={(e) => set('subject', e.target.value as any)}>
            <option value="math">数学</option>
            <option value="physics">物理</option>
          </select>
        </Field>
        <Field label="学段">
          <select value={value.grade_band} onChange={(e) => set('grade_band', e.target.value as any)}>
            <option value="junior">初中</option>
            <option value="senior">高中</option>
          </select>
        </Field>
        <Field label="难度 (1–5)">
          <input
            type="number"
            min={1}
            max={5}
            value={value.difficulty}
            onChange={(e) => set('difficulty', Number(e.target.value))}
            style={{ width: 72 }}
          />
        </Field>
      </div>

      <div className="editor-section-title">题目内容</div>
      <MathField
        label="题干 (LaTeX)"
        rows={5}
        value={value.question_text}
        onChange={(v) => set('question_text', v)}
      />
      <div className="editor-row editor-row-2">
        <MathField
          label="已知 (每行一个)"
          rows={3}
          value={value.given.join('\n')}
          previewAsList
          onChange={(v) => set('given', v.split('\n').filter(Boolean))}
        />
        <MathField
          label="求解 (每行一个)"
          rows={3}
          value={value.find.join('\n')}
          previewAsList
          onChange={(v) => set('find', v.split('\n').filter(Boolean))}
        />
      </div>
      <MathField
        label="图形描述"
        rows={4}
        value={value.diagram_description}
        onChange={(v) => set('diagram_description', v)}
      />

      <div className="editor-section-title">分类与标签</div>
      <Field label="知识点路径 (以 › 分隔)">
        <input
          type="text"
          value={value.topic_path.join(' › ')}
          onChange={(e) =>
            set('topic_path', e.target.value.split('›').map((s) => s.trim()).filter(Boolean))
          }
        />
      </Field>
      <Field label="标签 (逗号分隔)">
        <input
          type="text"
          value={value.tags.join(', ')}
          onChange={(e) =>
            set('tags', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))
          }
        />
      </Field>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="field-label">{label}</span>
      {children}
    </div>
  );
}

/** Textarea with an explicit MathJax-rendered preview next to it. */
function MathField({
  label,
  value,
  rows,
  previewAsList = false,
  onChange,
}: {
  label: string;
  value: string;
  rows: number;
  previewAsList?: boolean;
  onChange: (v: string) => void;
}) {
  const lines = value.split('\n').map((line) => line.trim()).filter(Boolean);
  const [editing, setEditing] = useState(false);

  return (
    <div>
      <div className="math-field-header">
        <span className="field-label" style={{ marginBottom: 0 }}>{label}</span>
        <button
          type="button"
          className="math-field-toggle"
          onClick={() => setEditing((prev) => !prev)}
        >
          {editing ? '收起原文' : '编辑原文'}
        </button>
      </div>
      <div className="math-field-grid math-field-grid-preview-only">
        {editing && (
          <textarea rows={rows} value={value} onChange={(e) => onChange(e.target.value)} />
        )}
        <div className="math-live-preview">
          <div className="math-live-preview-title">MathJax 预览</div>
          {value.trim() ? (
            previewAsList ? (
              <ul className="preview-list preview-list-tight">
                {lines.map((line, index) => (
                  <li key={index}><RichText text={line} /></li>
                ))}
              </ul>
            ) : (
              <RichText text={value} />
            )
          ) : (
            <span className="math-live-empty">输入后将在这里以数学排版显示</span>
          )}
        </div>
      </div>
    </div>
  );
}
