import { redirect } from "next/navigation";

// 홈("/") → 대화형 ask 가 LinkMind 메인 사용 경로 (2026-05-29 사용자 결정).
// 옛 그래프 페이지는 /graph 로 보류 (nav 에서 제거, URL 직접 접근은 가능).
export default function Home() {
  redirect("/ask");
}
