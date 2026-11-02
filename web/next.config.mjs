/** @type {import('next').NextConfig} */
const API = process.env.API_URL || "http://127.0.0.1:8000";

const nextConfig = {
  // the browser only talks to Next; Next forwards /api to the Python server, so the session
  // cookie stays first-party and no CORS setup is needed
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
