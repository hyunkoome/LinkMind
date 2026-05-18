"use client";

import { useCallback, useEffect, useState } from "react";

import {
  activatePromptVersion,
  getLLMSettings,
  listModels,
  listPromptVersions,
  savePromptVersion,
  updateLLMSettings,
} from "@/lib/api";
import type {
  LLMSettings,
  ModelsListResponse,
  PromptVersion,
} from "@/types/graph";

const PROMPT_NAMES = ["summary_system", "rag_system"] as const;
type PromptName = (typeof PROMPT_NAMES)[number];

export default function SettingsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [models, setModels] = useState<ModelsListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, m] = await Promise.all([getLLMSettings(), listModels()]);
      setSettings(s);
      setModels(m);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (loading && !settings) {
    return <main className="p-6 text-sm text-zinc-500">로딩 중…</main>;
  }

  return (
    <main className="h-full overflow-y-auto p-6 max-w-4xl mx-auto w-full">
      <h1 className="text-xl font-semibold mb-1">Settings</h1>
      <p className="text-sm text-zinc-500 mb-6">
        LLM provider/model 런타임 설정 + system prompt 버전 관리. env/dev.env 는
        시드 값이고 여기서 override 가 우선 (DB 의 app_settings + prompts 테이블).
      </p>

      {error && (
        <div className="mb-4 text-sm text-red-500">에러: {error}</div>
      )}

      {/* LLM provider/model */}
      <LLMSection
        settings={settings}
        models={models}
        onChanged={() => void reload()}
      />

      {/* Prompts */}
      {PROMPT_NAMES.map((name) => (
        <PromptSection key={name} name={name} onChanged={() => void reload()} />
      ))}
    </main>
  );
}

function LLMSection({
  settings,
  models,
  onChanged,
}: {
  settings: LLMSettings | null;
  models: ModelsListResponse | null;
  onChanged: () => void;
}) {
  const effective = settings?.effective || {};
  const [provider, setProvider] = useState(effective.default_llm_provider || "ollama");
  const [ollamaModel, setOllamaModel] = useState(effective.ollama_model || "");
  const [openaiModel, setOpenaiModel] = useState(effective.openai_model || "");
  const [anthropicModel, setAnthropicModel] = useState(effective.anthropic_model || "");
  const [saving, setSaving] = useState(false);

  // settings 가 reload 되면 state 동기화
  useEffect(() => {
    const e = settings?.effective || {};
    setProvider(e.default_llm_provider || "ollama");
    setOllamaModel(e.ollama_model || "");
    setOpenaiModel(e.openai_model || "");
    setAnthropicModel(e.anthropic_model || "");
  }, [settings]);

  const save = async () => {
    setSaving(true);
    try {
      await updateLLMSettings({
        default_llm_provider: provider,
        ollama_model: ollamaModel || null,
        openai_model: openaiModel || null,
        anthropic_model: anthropicModel || null,
      });
      onChanged();
    } catch (e) {
      alert(`저장 실패: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const ollamaList = models?.ollama?.models || [];

  return (
    <section className="mb-8 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-4">
      <h2 className="text-sm font-medium mb-3">LLM Provider / Model</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
        <label className="block">
          <span className="text-xs text-zinc-500">Default provider</span>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="mt-1 w-full px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded"
          >
            <option value="ollama">ollama (로컬)</option>
            <option value="openai">openai</option>
            <option value="claude">claude (anthropic)</option>
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-zinc-500">
            Ollama model{" "}
            {models?.ollama?.error && (
              <span className="text-red-400">({models.ollama.error})</span>
            )}
          </span>
          {ollamaList.length > 0 ? (
            <select
              value={ollamaModel}
              onChange={(e) => setOllamaModel(e.target.value)}
              className="mt-1 w-full px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded"
            >
              <option value="">(env 기본값)</option>
              {ollamaList.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={ollamaModel}
              onChange={(e) => setOllamaModel(e.target.value)}
              placeholder="qwen2.5:14b"
              className="mt-1 w-full px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded"
            />
          )}
        </label>
        <label className="block">
          <span className="text-xs text-zinc-500">OpenAI model</span>
          <input
            type="text"
            value={openaiModel}
            onChange={(e) => setOpenaiModel(e.target.value)}
            placeholder="gpt-4o-mini"
            className="mt-1 w-full px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded"
          />
        </label>
        <label className="block">
          <span className="text-xs text-zinc-500">Anthropic model</span>
          <input
            type="text"
            value={anthropicModel}
            onChange={(e) => setAnthropicModel(e.target.value)}
            placeholder="claude-haiku-4-5"
            className="mt-1 w-full px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded"
          />
        </label>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <div className="text-[10px] text-zinc-400">
          빈 값 → env 기본값으로 복귀 (override 해제)
        </div>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="px-3 py-1.5 text-xs bg-orange-500 hover:bg-orange-600 text-white rounded disabled:opacity-50"
        >
          {saving ? "저장 중…" : "저장"}
        </button>
      </div>
    </section>
  );
}

function PromptSection({
  name, onChanged,
}: {
  name: PromptName;
  onChanged: () => void;
}) {
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listPromptVersions(name);
      setVersions(list);
      const active = list.find((v) => v.is_active);
      if (active) setDraft(active.content);
    } catch (e) {
      alert(`prompt ${name} 로딩 실패: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const saveNew = async () => {
    if (!draft.trim()) return;
    setBusy(true);
    try {
      await savePromptVersion(name, { content: draft, note: note || undefined });
      setNote("");
      await reload();
      onChanged();
    } catch (e) {
      alert(`저장 실패: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const activate = async (version: string) => {
    setBusy(true);
    try {
      await activatePromptVersion(name, version);
      await reload();
      onChanged();
    } catch (e) {
      alert(`활성화 실패: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const active = versions.find((v) => v.is_active);

  return (
    <section className="mb-8 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-4">
      <h2 className="text-sm font-medium mb-2">
        Prompt · <code className="text-orange-600 dark:text-orange-400">{name}</code>
        {active && (
          <span className="ml-2 text-[10px] text-zinc-500">
            active: {active.version}
          </span>
        )}
      </h2>

      {loading ? (
        <div className="text-xs text-zinc-500">로딩 중…</div>
      ) : (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={10}
            className="w-full text-xs px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded font-mono"
            placeholder={`${name} system prompt…`}
          />
          <div className="mt-2 flex items-center gap-2">
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="변경 사유 (선택)"
              className="flex-1 px-2 py-1 text-xs bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded"
            />
            <button
              type="button"
              onClick={saveNew}
              disabled={busy || !draft.trim() || draft === active?.content}
              className="px-3 py-1 text-xs bg-orange-500 hover:bg-orange-600 text-white rounded disabled:opacity-50"
            >
              {busy ? "저장 중…" : "새 버전 저장 + 활성화"}
            </button>
          </div>

          {versions.length > 1 && (
            <details className="mt-3">
              <summary className="text-[10px] uppercase tracking-wider text-zinc-500 cursor-pointer">
                버전 히스토리 ({versions.length})
              </summary>
              <ul className="mt-2 space-y-1">
                {versions.map((v) => (
                  <li
                    key={v.version}
                    className="text-xs flex items-center gap-2 py-1 px-2 bg-zinc-50 dark:bg-zinc-800 rounded"
                  >
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded ${
                        v.is_active
                          ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700"
                          : "text-zinc-500"
                      }`}
                    >
                      {v.version}
                    </span>
                    <span className="text-zinc-500 text-[10px]">
                      {new Date(v.created_at).toLocaleDateString("ko-KR")}
                    </span>
                    {v.note && <span className="text-zinc-600 dark:text-zinc-400">{v.note}</span>}
                    <span className="ml-auto flex gap-2">
                      <button
                        type="button"
                        onClick={() => setDraft(v.content)}
                        className="text-[10px] text-blue-600 dark:text-blue-400 hover:underline"
                      >
                        편집창에 불러오기
                      </button>
                      {!v.is_active && (
                        <button
                          type="button"
                          onClick={() => activate(v.version)}
                          disabled={busy}
                          className="text-[10px] text-orange-600 dark:text-orange-400 hover:underline disabled:opacity-50"
                        >
                          이 버전 활성화
                        </button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
