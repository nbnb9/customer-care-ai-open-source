import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Next's file tracer can omit dynamically resolved ESM helpers from pnpm's
  // symlinked store. Include the package explicitly so the standalone image is
  // self-contained rather than depending on a build-stage node_modules tree.
  outputFileTracingIncludes: {
    "/*": ["node_modules/@swc/helpers/**/*"],
  },
  // Playwright uses a loopback host during local E2E. Keep Next 16's dev-origin
  // protection enabled while allowing only the two loopback aliases we support.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
