import type { ReactNode } from 'react';
import Link from 'next/link';
import Script from 'next/script';
import './globals.css';

export const metadata = {
  title: 'HAnswer · 学习伙伴',
  description: '数学 & 物理题目讲解与练习',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="app-header">
          <Link href="/" className="app-logo">HAnswer</Link>
          <span className="app-logo-sub">学习伙伴</span>
          <nav className="app-nav">
            <Link href="/" className="nav-link">提问</Link>
            <Link href="/dialog" className="nav-link">对话</Link>
            <Link href="/library" className="nav-link">题库</Link>
            <Link href="/practice" className="nav-link">练习</Link>
            <Link href="/knowledge" className="nav-link">知识</Link>
            <Link href="/settings" className="nav-link">设置</Link>
          </nav>
        </header>
        <main className="app-main">{children}</main>

        {/* MathJax 3 — config must be set before the script loads */}
        <Script
          id="mathjax-config"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{
            __html: `
              window.MathJax = {
                tex: {
                  inlineMath: [['$', '$']],
                  displayMath: [['$$', '$$']],
                  processEscapes: true,
                  tags: 'none',
                },
                options: {
                  skipHtmlTags: ['script','noscript','style','textarea','pre','code'],
                  ignoreHtmlClass: 'tex2jax_ignore',
                },
                startup: {
                  typeset: false,
                  ready() {
                    MathJax.startup.defaultReady();
                    document.dispatchEvent(new Event('mathjax-ready'));
                  },
                },
              };
            `,
          }}
        />
        <Script
          src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"
          strategy="afterInteractive"
        />
      </body>
    </html>
  );
}
