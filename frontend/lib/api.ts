export function apiUrl(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  const explicitBase = process.env.NEXT_PUBLIC_API_BASE?.trim();
  if (explicitBase) {
    return `${explicitBase.replace(/\/$/, '')}${normalized}`;
  }

  if (typeof window !== 'undefined' && window.location.port === '3333') {
    return `http://127.0.0.1:8787${normalized}`;
  }

  return normalized;
}
