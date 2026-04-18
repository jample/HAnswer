import type { ReactNode } from 'react';
import Link from 'next/link';

export const metadata = {
  title: 'HAnswer · 学习伙伴',
  description: '数学 & 物理题目讲解与练习',
};

const navLink = { padding: '4px 8px', color: '#0366d6', textDecoration: 'none' } as const;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body style={{ margin: 0, fontFamily: 'system-ui, -apple-system, sans-serif' }}>
        <header
          style={{
            padding: '12px 20px',
            borderBottom: '1px solid #eee',
            display: 'flex',
            gap: 12,
            alignItems: 'baseline',
          }}
        >
          <strong style={{ fontSize: 18 }}>HAnswer</strong>
          <span style={{ color: '#888' }}>· 学习伙伴</span>
          <nav style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
            <Link href="/" style={navLink}>提问</Link>
            <Link href="/library" style={navLink}>题库</Link>
            <Link href="/practice" style={navLink}>练习</Link>
            <Link href="/knowledge" style={navLink}>知识</Link>
            <Link href="/settings" style={navLink}>设置</Link>
          </nav>
        </header>
        <main style={{ padding: 20 }}>{children}</main>
      </body>
    </html>
  );
}
