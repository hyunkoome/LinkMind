"use client";

import { useEffect, useState } from "react";

import { getLLMSettings } from "@/lib/api";

/**
 * DB(app_settings)의 effective vLLM 모델명을 표시. 헤더 등에 인라인으로 쓴다.
 * 모델 id 의 provider 접두(cyankiwi/ 등)는 떼고 모델명만 보여준다.
 * 예: cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit → gemma-4-26B-A4B-it-AWQ-4bit
 */
export default function ModelLabel() {
  const [model, setModel] = useState("");
  useEffect(() => {
    getLLMSettings()
      .then((s) => setModel(s.effective?.vllm_model || ""))
      .catch(() => {});
  }, []);
  const short = model.includes("/") ? model.split("/").pop()! : model;
  return <>{short || "…"}</>;
}
