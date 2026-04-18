/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: '/api/:path*', destination: 'http://127.0.0.1:8787/api/:path*' },
    ];
  },
  async headers() {
    return [
      {
        // Strict CSP for the viz sandbox HTML (§3.3.2).
        source: '/viz/sandbox.html',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'none'",
              "script-src 'self' 'unsafe-inline'",
              "style-src 'self' 'unsafe-inline'",
              "img-src data:",
              "connect-src 'none'",
              "frame-ancestors 'self'",
            ].join('; '),
          },
        ],
      },
    ];
  },
};
module.exports = nextConfig;
