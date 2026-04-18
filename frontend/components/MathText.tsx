'use client';

import { useEffect, useRef } from 'react';

declare global {
  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    MathJax: any;
  }
}

/**
 * Imperatively typeset `el` with MathJax 3.
 *
 * Key invariant: the caller must set `el.textContent` (or `el.innerHTML`)
 * to the raw source text BEFORE calling this function.  MathJax then scans
 * the plain text for $…$ / $$…$$ and replaces it with rendered output.
 *
 * We do NOT use the `typesetClear` + JSX-children pattern because React and
 * MathJax both mutate the same DOM node, leading to conflicts where MathJax's
 * rendered HTML is partially overwritten by React's reconciler.
 */
function typesetElement(el: HTMLElement) {
  if (typeof window === 'undefined') return;

  const doTypeset = () => {
    const mj = window.MathJax;
    if (mj?.typesetPromise) {
      mj.typesetPromise([el]).catch(() => {});
    }
  };

  const mj = window.MathJax;
  if (mj?.startup?.promise) {
    // MathJax is configured — wait for startup to complete before typesetting
    mj.startup.promise.then(doTypeset);
  } else if (mj?.typesetPromise) {
    doTypeset();
  } else {
    // MathJax CDN hasn't loaded yet — queue on the ready event emitted by layout.tsx
    document.addEventListener('mathjax-ready', doTypeset, { once: true });
  }
}

/**
 * Renders arbitrary text containing $…$ inline or $$…$$ display math.
 *
 * Content is set imperatively via `el.textContent` so React never touches the
 * DOM node after mount — MathJax has sole ownership of the node's innerHTML.
 */
export function RichText({ text }: { text: string }) {
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 1. Restore plain source text, wiping any previous MathJax output
    el.textContent = text;
    // 2. Ask MathJax to typeset the fresh plain text
    typesetElement(el);
  }, [text]);

  // Render an empty span — content is owned by the effect above, not React
  return <span ref={ref} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'break-word', maxWidth: '100%', display: 'inline-block' }} />;
}

/**
 * Renders a single LaTeX expression, optionally in display (block) mode.
 * Wraps the source in $…$ or $$…$$ delimiters for MathJax.
 */
export function TeX({ src, block = false }: { src: string; block?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.textContent = block ? `$$${src}$$` : `$${src}$`;
    typesetElement(el);
  }, [src, block]);

  return <span ref={ref} />;
}

