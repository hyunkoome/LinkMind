import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 좌하단 N (Next.js dev indicator) 끔 — 메뉴 텍스트가 Next 가 직접 렌더해서
  // LinkMind 의 한/EN 토글로 통제 불가. dev 화면에서도 깔끔하게 비활성.
  devIndicators: false,
};

export default nextConfig;
