// Static export for Tauri bundling — the desktop shell loads this build
// via the tauri:// protocol, not a Node server.
// See src-tauri/README.md and docs/DECISIONS.md §3.7.
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
