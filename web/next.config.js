/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig = {
  async rewrites() {
    // Same-origin /backend for the browser; avoids CORS entirely in dev.
    return [
      { source: "/backend/:path*", destination: `${backend}/:path*` },
    ];
  },
};
module.exports = nextConfig;
