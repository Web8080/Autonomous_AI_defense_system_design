/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
  async redirects() {
    return [
      { source: "/simulation", destination: "/dashboard/simulation", permanent: false },
      { source: "/simulations", destination: "/dashboard/simulation", permanent: false },
    ];
  },
};

module.exports = nextConfig;
