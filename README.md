<div align="center">

# LinkMind

**A self-contained personal AI engine that preserves everything you collect (via Telegram / URL) raw-first, auto-synthesizes it into an llm_wiki, and lets you ask questions conversationally**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-1.12-DC244C)
![vLLM](https://img.shields.io/badge/vLLM-Gemma%204%2026B--A4B-FFCE00)

**English** | [한국어](README.ko.md)

</div>

---

## Overview

**LinkMind is an on-premise AI engine that gathers your scattered material into one place and turns it into "your own knowledge."** Drop a link into Telegram or paste a URL, and LinkMind preserves the original losslessly (AI summary + embedding), groups related material into auto-synthesized **llm_wiki** pages, and lets you query everything through conversational RAG (`/ask`).

backend · agent · UI all live in **one self-contained system** in a single repository, so a single self-host install brings everything along — no external client agent required.

But LinkMind itself is a **means to an end**. The real goal is to use the data you accumulate to **LoRA-fine-tune an sVLL (small Vision-Language LLM)** into your own personalized AI engine, and to keep retraining it (a continuous training loop). Every design decision therefore has to pass one question: **"Does this help preserve / structure / export training data?"**

> **Deployment strategy**: self-host first, as the default mode. Long term, OSS (AGPL v3) release + an optional hosted SaaS. But **training the operator's shared model on user data is strictly forbidden** — a personal LoRA is "your data, for your model, only."

---

## ✨ Features

<details open>
<summary><b>📥 Ingest — raw-first, lossless preservation</b></summary>

<br>

- **Many sources**: URL / PDF / DOCX / PPTX / TXT / MD / GitHub / arxiv / YouTube (video · playlist · channel) / images — a host-based auto-routing dispatcher.
- **raw-first**: lossless original storage + provenance tracking + idempotent (UNIQUE hash blocks duplicates) + SHA-256 attachment dedup, kept forever.
- **Fallback safety net**: even if body extraction fails, raw + URL are always preserved (OG meta / YouTube oEmbed fallback, `fetch_error` marking).
- **arxiv / IEEE / DOI URLs** yield the real paper title. The same subject (arxiv_id / github_repo / doi / yt_id) is automatically merged into one topic.
- **Telegram multi-channel inbox watcher**: yaml as single source of truth, FloodWait/cache handling, deletes the message on successful processing.
- **Slack export one-shot backfill**: thread / mrkdwn / attachment parser.
- **AI summary** (Korean bullets, Gemma 4) + **embedding** (bge-m3, 1024-dim, shared over HTTP via vLLM-embed).

</details>

<details>
<summary><b>📚 Wiki (llm_wiki) — LinkMind's identity</b></summary>

<br>

Not plain chunk-RAG, but a **karpathy llm_wiki + multi-agent** pattern.

- **4 agents**: `classifier` (material → wiki mapping) / `retriever` / `writer` (doc-type-aware markdown synthesis) / `critic` (stub).
- **Automatic flow**: new ingest → `analysis_worker` (summary) → `classifier` (mapping + pending) → `wiki_writer_worker` daemon (auto synthesis).
- **Doc-type-aware writer**: papers (arxiv/pdf) get a **paper structure** (overview / contributions / method / experiments·results / conclusion) synthesized from the **Docling raw markdown** (not just the summary) so the wiki is rich enough to grasp without the original. Figures are placed **inline in context** (system-overview figure → overview, architecture → method, results → experiments) via `[FIGN]` placeholders the code resolves to real images; tables are kept inline (English cells + Korean caption); figure captions are summarized to one Korean line (number preserved). Other material keeps the concept-style structure.
- **1 link = 1 wiki**: only a self-identity topic with confidence ≥ 0.9 becomes a primary wiki, guaranteed by `native_identity_external_id`. Cross-modal clues (0.7) only create links. The classifier also **skips a redundant self-wiki** when the item already maps to a concept/external wiki (de-dup prevention), and a self-wiki's title/body/figures stay focused on **its own identity item** (cross-linked papers stay related-only, not cloned).
- **Keywords**: normalization (English only + camelCase + acronyms/aliases, e.g. `LiDAR→lidar`, `3D Gaussian Splatting→3dgs`) + a cloud sidebar (frequency-sorted + ⭐ + multi-AND filter). Editable in Settings + DB.
- **Photos**: "photo + URL" becomes an in-body figure link, standalone photo wikis are cleaned up (raw preserved). A photo-only message is not ingested.
- **3 statuses**: `issues` (leftover/failed) / `pending` (processing queue) / `completed` (clickable). A sub-state `body_processing_started_at` distinguishes generating vs queuing.
- **Rendering**: wiki bodies render via react-markdown — GFM **tables** + **LaTeX** (KaTeX) + `[[slug]]` wikilinks + `[N]` citations + backend-served figures.
- **Tools**: backfill (concurrency 4) + cleanup jobs (`cleanup_duplicate_wikis` with T1 self→external / T2 phantom / **T4 self→concept** merge / `normalize_keywords` / `link_photo_captions`).

</details>

<details>
<summary><b>💬 Conversational RAG (/ask)</b></summary>

<br>

- Home (`/`) redirects to `/ask` — LinkMind's main path.
- 3-panel UI: left sidebar (projects + recent chats, localStorage-persisted) / center chat (citation chips + related_wikis) / right inline detail of the clicked wiki — drag-resizable borders.
- Answers are **always grounded in DB material** (RAG) — not a generic LLM response.
- **Multi-turn conversation**: prior turns are sent as history for context retention, follow-up questions are rewritten into standalone search queries (condense), and answers stream token-by-token over SSE (`POST /ask/stream`). Paste a URL to auto-ingest it and ground the answer in that material.
- **Hybrid RAG (2026-06-03)**: answers now draw on **wiki bodies** (`linkmind_wiki_pages`) + item chunks + pinned items together — the curated wikis you built are actually used in answers, not just raw chunks.
- **Conversation privacy**: sessions/messages are visible only to their owner (even admins can't see others'); projects are org-shared. Per-account localStorage + server sync (`PUT /sessions/sync`, restore via `GET /sessions/export`).
- Next: **arxiv external search (agentic)** — local Gemma + the free arxiv API (no external AI); then a search / QA / agentic-action request-type split.

</details>

<details>
<summary><b>🔐 Multi-tenant / Auth</b></summary>

<br>

- **1 org = 1 space, shared data**; no self-signup (a root admin issues members, or bootstrap). A space is the unit of isolation, learning, and responsibility.
- **Auth**: id/pw login + JWT in an httpOnly cookie + every data API protected. **bootstrap**: when there are 0 users, the browser `/login` shows "create organization" to make the first admin + org.
- **Member issuance**: a root admin issues accounts (email + initial password) in Settings (root-only `require_space_admin`); the member is forced to change credentials on first login (force-change).
- **Conversation privacy**: a session is visible only to its owner; the whole space is used for training (viewing ≠ training).
- **Deployment model**: one org server (backend + GPU + DB) + thin **web/desktop (Tauri)** clients — user machines need no GPU. Per-row RLS is intentionally dropped (1 org = 1 instance = the isolation boundary).

</details>

<details>
<summary><b>🖥️ UI (Next.js 16 + React 19)</b></summary>

<br>

- `/ask` — conversational RAG (main · home)
- `/wiki` — list (keyword cloud + status/sort/search filters + 4 tabs + pagination + per-page radio + ETA + 7-field search) + a right-side inline detail panel (`WikiDetailView`: edit / re-synthesize / Sources / Relationship / Keywords / meta / delete)
- `/wiki/[slug]` — standalone detail page (shares `WikiDetailView`)
- `/ingest` · `/settings` (edit keyword acronyms/aliases)
- i18n (KO/EN) + ThemeToggle (☀️/🌙/🖥) + material/wiki deletion (2-step confirm, Qdrant + Postgres CASCADE, **raw preserved**)
- `/graph` (keyword relation graph) is on hold — removed from main nav, direct-URL only (code & endpoint kept).

</details>

---

## 🧩 Architecture

A single deployment unit that keeps backend + agent + UI in one repository. Every module shares the same venv · same Postgres · same Qdrant.

```
┌─────────────────────────────────────────────────────────────┐
│                        frontend/ (Next.js 16)                │
│   /ask · /wiki · /wiki/[slug] · /ingest · /settings  :3001   │
└───────────────────────────────┬─────────────────────────────┘
                                 │ HTTP
┌───────────────────────────────▼─────────────────────────────┐
│                     backend/ (FastAPI)  :8000                │
│  api: /ingest /search /ask /wiki /graph /items /categories   │
│       /files /settings /health   (/docs Swagger)             │
│  ingest dispatcher · 4 wiki agents · analysis_worker         │
│  wiki_writer_worker daemon · LLM provider abstraction        │
└──────┬─────────────────────┬──────────────────┬─────────────┘
       │                     │                  │
┌──────▼──────┐      ┌───────▼──────┐    ┌──────▼──────────────┐
│ PostgreSQL  │      │   Qdrant     │    │  vLLM (Gemma 4)     │
│ 16          │      │   1.12       │    │  vLLM-embed (bge-m3)│
│ raw + rels  │      │ vector search│    │  :8002 /v1/embed... │
└─────────────┘      └──────────────┘    └─────────────────────┘
       ▲
┌──────┴──────────────────────────────────────────────────────┐
│         ai_agents/  — multi-channel inbox watcher            │
│   telegram_inbox_watcher (now) → slack/whatsapp/discord      │
│   ※ never calls the LLM directly — goes through HTTP /ask    │
└──────────────────────────────────────────────────────────────┘
```

| Module | Role |
|---|---|
| **`backend/`** | FastAPI HTTP API, DB, embedding, LLM provider, ingest modules, wiki agents, worker daemon |
| **`ai_agents/`** | Multi-channel inbox/gateway daemons. Call the backend HTTP API (never the LLM directly) |
| **`frontend/`** | Next.js 16 App Router + React 19 + Tailwind v4 + react-force-graph-3d + three.js |

**Tech stack**: Python 3.11+ (verified on 3.13.12 + torch 2.6.0+cu124) · FastAPI · SQLAlchemy 2.0 async + asyncpg · pydantic-settings · PostgreSQL 16 · Qdrant 1.12 · vLLM (Gemma 4 26B-A4B MoE-AWQ, KV cache fp8 + 16384 context) · sentence-transformers (bge-m3) · NVIDIA GPU (≥24GB VRAM recommended, 24GB minimum — e.g. RTX 4090) · Docker + nvidia-container-toolkit.

> **The default LLM is vLLM (Gemma 4).** OpenAI / Anthropic / Ollama are also supported through the provider abstraction, but they are optional. Model and runtime settings are managed in the DB (`app_settings`) + Settings UI, and restarted via `scripts/vllm_restart.sh`.

---

## 🚀 Quickstart

> **Prerequisites**: Ubuntu (or WSL2), NVIDIA GPU with **≥24GB VRAM recommended (24GB minimum)** — e.g. RTX 4090, Docker 24+ + nvidia-container-toolkit. All settings live in the `env/dev.env` environment file (copy from `env/dev.env.example`).

<details open>
<summary><b>step1 — Python base environment</b></summary>

```bash
bash scripts/step1_install_base_env.sh        # .venv + torch cu124 + requirements
source .venv/bin/activate
bash scripts/step1_check_base_env.sh
```

</details>

<details>
<summary><b>step2 — Docker + infrastructure (Postgres + Qdrant)</b></summary>

```bash
# step2_1: install Docker + NVIDIA Container Toolkit on the host (sudo, once)
bash scripts/step2_1_install_docker.sh
bash scripts/step2_1_check_docker.sh          # after a new shell or 'newgrp docker'

# step2_2: bring up LinkMind infrastructure
bash scripts/step2_2_setup_infra.sh
bash scripts/step2_2_check_infra.sh
```

</details>

<details>
<summary><b>step3 — Qdrant collection + vLLM (default LLM)</b></summary>

```bash
# Qdrant collection (first run downloads bge-m3, ~1.4GB)
python -m backend.jobs.init_qdrant
bash scripts/step4_check_qdrant.sh

# vLLM (Gemma 4 26B-A4B-AWQ, the default LLM) + vLLM-embed (bge-m3) containers
docker compose --env-file env/dev.env -f compose/docker-compose.dev.yml \
    --profile vllm up -d
# Model & runtime params are managed in the DB (app_settings) + Settings UI;
# restart with scripts/vllm_restart.sh
```

</details>

<details>
<summary><b>(optional) Ollama provider — only if you use it instead of vLLM</b></summary>

```bash
# The default LLM is vLLM (Gemma 4). Ollama is just an optional fallback in the
# provider abstraction — skip this step if vLLM is enough.
bash scripts/step3_setup_ollama.sh            # pulls OLLAMA_MODEL from env
bash scripts/step3_check_ollama.sh
```

</details>

<details open>
<summary><b>step5 — run everything (backend + frontend + telegram watcher)</b></summary>

```bash
bash scripts/step5_run_dev.sh                 # all three at once (recommended)
bash scripts/step5_run_dev.sh --status        # check status
bash scripts/step5_run_dev.sh --stop          # stop
```

</details>

Once running:

| Service | URL |
|---|---|
| Frontend (Next.js) | http://localhost:3001 |
| Backend API (Swagger) | http://localhost:8000/docs |
| vLLM-embed (bge-m3) | http://localhost:8002/v1/embeddings |

---

## 💡 Usage

**Ingest a single URL manually**

```bash
python -m backend.ingest.url https://arxiv.org/abs/2401.01234
```

**Telegram inbox watcher** — drop a link into the channel and it auto-runs ingest → summary → wiki synthesis

```bash
python -m ai_agents.telegram_inbox_watcher                # auto backfill → listen (default)
python -m ai_agents.telegram_inbox_watcher --no-backfill  # listen only, no backfill
python -m ai_agents.telegram_inbox_watcher --backfill 50 --no-listen  # process last 50 in bulk
```

> step5 (`bash scripts/step5_run_dev.sh`) auto-starts this watcher in the background alongside backend & frontend.

**Conversational questions (`/ask`)** — ask in natural language at http://localhost:3001/ask. Answers are RAG-generated from your accumulated DB material, shown with citation chips · related_wikis. Click a wiki card to open its detail inline in the right panel.

**Check wiki status**

```bash
curl -s http://localhost:8000/wiki/_meta/stats | jq      # completed / pending / issues
```

**Wiki backfill / cleanup (run by you; all idempotent — dry-run first)**

```bash
bash scripts/run_wiki_backfill.sh                          # bulk-synthesize wiki bodies for old pages (concurrency 4)
bash scripts/run_wiki_backfill.sh --status                 # progress summary
python -m backend.jobs.cleanup_duplicate_wikis --dry-run   # preview duplicate-wiki cleanup (T1 self→external / T2 phantom / T4 self→concept)
python -m backend.jobs.normalize_keywords --dry-run        # preview keyword normalization
python -m backend.jobs.link_photo_captions --dry-run       # preview photo-figure linking
```

---

## 🔒 The 5 Data Principles

Analysis results (summary, embedding) can be regenerated, but if the raw breaks it cannot be recovered. **Always store the raw first, analyze afterward.**

| Principle | Meaning | Enforced at |
|---|---|---|
| **Raw-first** | Lossless preservation of original text/files | `items.raw_content NOT NULL` |
| **Provenance** | Track source_type / source_url / source_id / hash | schema `NOT NULL` constraints |
| **Idempotent** | No duplicate storage of the same material | `UNIQUE(source_type, raw_content_hash)` |
| **Versioned analysis** | Record model version on summary/embedding | `summary_model`, `embedding_model` columns |
| **Loss-less storage** | No resize/compress of images/PDFs | `attachments.file_hash` as-is |

---

## 🗺️ Roadmap

| Phase | Status | Highlights |
|---|---|---|
| **1** | ✅ Done | Postgres + Qdrant + URL ingest + Embedding + Semantic Search + RAG |
| **2** | ✅ Done | AI summary/tagging, Slack export parser, embedding infra (vLLM-embed), category enrichment, Topic graph, ChannelAgent ABC, Next.js 16 + react-force-graph-3d UI, modality-aware viewer, 3-tier categories, Telegram multi-channel |
| **3** | ✅ Done | **llm_wiki system** — classifier/retriever/writer agents, wiki API + Qdrant body search, wiki list/detail UI + KeywordsEditor, writer daemon + batch backfill, "1 link = 1 wiki", keyword normalization/cloud, photo-figure linking, conversational `/ask` (Step 1) |
| **4** | 🚧 In progress | **Multi-tenant (org space / member issuance / force-change / permissions / conversation privacy) ✅**, conversational `/ask` multi-turn ✅ + **hybrid RAG (wiki bodies) ✅**, **paper-aware writer redesign ✅** (Docling raw → paper structure + inline figures/tables + Korean captions) + **duplicate-wiki cleanup (T4) & prevention + de-clone ✅** + wiki rendering (GFM tables / LaTeX), **keyword-driven arxiv collection → wikis (admin) next**; real channel expansion (Slack/WhatsApp/Discord), self-learning (**implicit feedback** — next-turn nuance/behavior as signal + explicit corrections; not 👍/👎 buttons), critic agent |
| **5** | ⬜ Not started | **sVLL LoRA fine-tuning** (Gemma 4 26B-A4B QLoRA or 12B dense), dataset exporter (raw + summary + feedback → JSONL), vLLM/Ollama serving |
| **6** | ⬜ Not started | Continuous training loop, complete on-premise AI engine |
| **7** | ⬜ Not started | OSS (AGPL v3) release → hosted SaaS (Auth.js + Stripe, multi-tenant, BYOK) |
| **8** | **T.B.D** | **Multimodal VLM** — the model *sees* figure images themselves to understand/describe them (caption-less figures / figure Q&A / OCR). Via Gemma 4 12B native multimodal or SmolVLM2. Raw images are already preserved losslessly (§2), so this can start anytime — decide once the need is confirmed. |

> Next priorities: **arxiv external search (agentic — local Gemma + the free arxiv API, no external AI)** → search/QA/agentic request-type split → **self-learning (feedback)** → **training pipeline (Phase 5)**. The training pipeline's *infrastructure* (dataset exporter, QLoRA setup, dataset quality checks) can be built early — in parallel with self-learning; actual training runs once enough data has accumulated.

---

## 🤝 Contributing

LinkMind is currently in a **solo-development phase** (focused on self-host completeness). There is no formal external contribution workflow yet. Please file bug reports, feature requests, and questions at [GitHub Issues](https://github.com/hyunkoome/LinkMind/issues) and they'll be reviewed.

Note: all docs and commit messages in this repository are written in Korean, and adding a new feature comes with a companion unit test by policy.

---

## 📜 License

LinkMind aims for a two-option license (currently in the self-host phase, so the commercial license is "inquiry-based").

- **AGPL-3.0** — free and unlimited for research · self-host · personal/internal company use. However, if you offer a modified version as a network service (SaaS), you must publish your source changes (the model adopted by Plausible / Cal.com / n8n). See [`LICENSE`](LICENSE) for the full terms.
- **Commercial license** — if you want to integrate LinkMind into a closed product or SaaS without AGPL-3.0's disclosure obligations, contact by email: **hyunkookim.me@gmail.com**

Copyright (C) 2026 Hyunkoo Kim ([@hyunkoome](https://github.com/hyunkoome)).

---

## 📞 Contact

- **GitHub Issues** — https://github.com/hyunkoome/LinkMind/issues
- **Email** — hyunkookim.me@gmail.com

<div align="center">

**LinkMind** — what you collect becomes, in the end, an AI that is yours alone.

</div>
