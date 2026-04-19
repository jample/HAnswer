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
        // Strict CSP for the JSXGraph viz sandbox HTML (§3.3.2).
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
      {
        // GeoGebra sandbox: needs the GeoGebra CDN allow-listed for
        // scripts, fonts, images and XHR (deployggb.js loads chunked
        // GWT assets). Still tight: only geogebra.org origins are added.
        source: '/viz/geogebra-sandbox.html',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.geogebra.org https://cdn.geogebra.org",
              "style-src 'self' 'unsafe-inline' https://www.geogebra.org https://cdn.geogebra.org",
              "font-src 'self' data: https://www.geogebra.org https://cdn.geogebra.org",
              "img-src 'self' data: blob: https://www.geogebra.org https://cdn.geogebra.org",
              "connect-src 'self' https://www.geogebra.org https://cdn.geogebra.org",
              "worker-src 'self' blob:",
              "frame-ancestors 'self'",
            ].join('; '),
          },
        ],
      },
    ];
  },
};
module.exports = nextConfig;
