# Plutus

Question answering over company filings, with citations you can check.

Upload a filing (10-K, 10-Q, 8-K) as either a **PDF or the HTML EDGAR serves**, ask a
question in plain English, and get an answer with the exact page it came from — or an
honest "not found in this filing" when the evidence isn't there.

![Plutus showing a verified answer beside the cited filing page](docs/screenshot.png)

## Why you can trust the answers

A system that occasionally answers confidently and wrongly is worse than one that
says "I don't know" more often. For an analyst, an unsupported figure that reaches a
model or a memo costs far more than a gap someone fills by hand. The pipeline is
therefore built, tuned and measured against that asymmetry — a correct cited answer
scores +1, an honest refusal scores 0, and a confident wrong answer scores −1, so
guessing loses. That scoring policy is enforced in code, not just in evaluation:
see `backend/app/qa/answer_service.py`.

Every answer has to clear three independent checks before you see it:

1. **Retrieval has to find something genuinely relevant.** A cross-encoder scores
   the question against each candidate passage; if nothing clears the bar, the model
   is never even called.
2. **The answer has to be produced**, either by computing it from figures the issuer
   tagged in its own filing, or by the model in a JSON schema that forces it to name
   a page and quote.
3. **The quote has to actually exist on the page it cites**, and any number in the
   answer has to actually appear in that quote — or be re-derivable from figures that
   do.

Fail any one and the answer becomes "not found in this filing". The model's own
confidence is never sufficient on its own — that's the entire point of step 3.

### Figures are computed, not read out

The single measured failure that shapes this system: asked for Activision's FY2019
fixed-asset turnover, the model read revenue 6,489 and PP&E 253/282 correctly off
two pages, averaged them correctly to 267.5, and then reported the quotient as
**24.77** when it is **24.26**. Every input right, the division wrong.

So a question that names a metric the system knows is **not** answered by the model.
The figures come from the filing's own inline-XBRL tags (or, for older filings, from
parsed statement rows), and the arithmetic happens in code, where it cannot be got
wrong. The model is asked the same question *in parallel*, and the two answers are
compared: agreement is recorded as corroboration, disagreement is logged and the
computed figure ships. Questions that are genuinely prose — "what risks does the
company disclose?" — still go to the model, which is what it is reliable at.

When it does decline, click **Show what was checked** to see the passages it
considered and how relevant they scored. A refusal you can inspect is far more
useful than an opaque shrug.

## Before you start: you need one API key

**This is the only thing you must obtain yourself.** Generation is a hosted API
call — there is no local language model to download or run — so the app cannot
answer questions until a key is configured.

Three values control it, all in `.env` at the repository root (copy
`.env.example` to `.env` first):

| Variable | What to put there |
|---|---|
| `LLM_API_KEY` | **Required.** Your key from the provider below. |
| `LLM_BASE_URL` | The provider's OpenAI-compatible endpoint. |
| `LLM_MODEL` | The model name as that provider spells it. |

### Getting a free OpenRouter key (about two minutes)

Any OpenAI-compatible provider works, but this is the one the app was tested on,
and the model it was tested with costs nothing to use.

**1. Create an account.** Go to <https://openrouter.ai> and sign up — Google,
GitHub or email all work. **No payment card is required** for the free model
used here.

**2. Create a key.** Open <https://openrouter.ai/keys> and press **Create Key**.

- *Name* — anything, e.g. `plutus-local`.
- *Credit limit* — **leave it blank.** It caps spending on paid models; the
  model below is free either way, and a limit of `0` can be rejected as
  invalid rather than treated as "free only".

**3. Copy it immediately.** The key is shown **once** and looks like
`sk-or-v1-` followed by 64 hex characters. If you lose it, delete the key and
make another — it cannot be displayed again.

**4. Put it in `.env`.** From the repository root:

```bash
cp .env.example .env      # already points at the tested model
```

Then open `.env` and paste the key after `LLM_API_KEY=`. Nothing else needs
changing — `.env.example` already carries the endpoint and model below.

**5. Check it works.**

```bash
curl https://openrouter.ai/api/v1/auth/key -H "Authorization: Bearer sk-or-v1-..."
```

A JSON object with `"is_free_tier": true` means the key is live. In the running
app the same check is in **Settings → Test connection**, which asks the model a
real grounded question rather than merely pinging it — a reachable endpoint
configured with a model name that does not exist is just as useless as an
unreachable one.

> **Why this model is free.** OpenRouter reports `minimax/minimax-m3:free` at
> `prompt: 0` and `completion: 0` — zero cost per token, verified against their
> models API, not assumed from the name. The `:free` suffix is what selects that
> tier; drop it and you are on the paid variant of the same model.
>
> Free routing is rate-limited and shared, so expect occasional `429`s and slower
> responses under load. The app retries short bursts automatically and reports a
> genuine daily exhaustion as *"AI usage limit reached"* rather than failing
> opaquely.

**This exact configuration is what every published figure in this repository was
measured on** — copy it verbatim and only replace the key:

```bash
# OpenRouter, MiniMax M3 free tier - the tested configuration
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=minimax/minimax-m3:free
LLM_API_KEY=sk-or-v1-...        # <- paste your own key here
```

| | value |
|---|---|
| Provider | OpenRouter |
| Endpoint | `https://openrouter.ai/api/v1` |
| Model ID | `minimax/minimax-m3:free` — note the `:free` suffix, which selects the free tier |
| Model page | <https://openrouter.ai/minimax/minimax-m3:free> |
| Context window | 1M tokens |
| Cost | Free (rate-limited; see the caveats below) |

Alternatives, if you already have an account elsewhere: [Groq](https://console.groq.com)
(`https://api.groq.com/openai/v1`) or
[Google AI Studio](https://aistudio.google.com/apikey)
(`https://generativelanguage.googleapis.com/v1beta/openai`). Both were tried;
neither is what the published numbers come from.

### For real accuracy, use a paid model

The free tiers are genuinely usable, and **every accuracy figure in this
repository was measured on one** — `minimax/minimax-m3:free`, the free tier on
OpenRouter. Read those numbers as a floor rather than a ceiling. The free tier is
also where the limits bite, measurably:

- **Free endpoints are not deterministic.** Two identical 136-question evaluation
  runs against `minimax/minimax-m3:free` — the free tier on OpenRouter, which is
  what every published figure here was measured on — same inputs and
  `temperature=0`, differed
  on **16 questions** — six of them flipping from a safe refusal to a confidently
  wrong answer. Free tiers load-balance across replicas that do not answer alike.
- **They break under load.** Malformed JSON, `429`s and daily caps are routine.
  The app retries and degrades honestly, but every retry is latency.
- **Capability is the ceiling on prose questions.** Judgement questions ("is this
  business cyclical?") are where a stronger model earns its cost.

**A paid tier of a frontier model is the single highest-leverage change available
to answer quality**, and it is a `.env` edit — no code changes:

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-sonnet-4.5      # or openai/gpt-5, google/gemini-3-pro
LLM_API_KEY=sk-or-v1-...
```

One caveat worth stating plainly: a stronger model does **not** improve the
computed figures, because those never go through a model. It improves the prose
half.

## Quick start

**On a machine with nothing installed**, double-click:

| | |
|---|---|
| **Windows** | `start.bat` |
| **macOS** | `start.command` |

On first launch, the script copies `.env.example` to `.env` and asks for an
OpenRouter API key. It keeps the configured endpoint and
`minimax/minimax-m3:free` model unchanged. Press Enter to skip the prompt and add
the key to `.env` later.

### What gets installed

The script installs anything missing — you do **not** need to install Docker, or
anything else, by hand:

| Software | Why | Installed by the script? |
|---|---|---|
| **Python 3.11+** | Runs the backend | Yes — winget (Windows) / Homebrew (macOS) |
| **Node 18+** | Builds and serves the UI | Yes — same |
| **Docker Desktop** | Runs Postgres and Qdrant | Yes — same. On Windows it may need one reboot before Docker can start. |
| Postgres 16, Qdrant | Filings, history, vectors | Yes — pulled as containers, nothing installed on the host |
| ONNX embedding + reranking models (~150MB) | Retrieval, in-process | Yes — downloaded on first backend start |

It then creates the virtual environment, installs both dependency trees, brings up
the containers, starts the app, waits until both services respond, and opens your
browser. It is idempotent — anything already present is detected and skipped, so a
second run takes seconds. If no API key is configured it offers to save one.
`stop.bat` / `stop.command` shut everything down without touching your data.

<details>
<summary>Or set it up by hand</summary>

Requires Python 3.11+, Node 18+ and Docker already installed.

```bash
docker compose up -d                     # Postgres + Qdrant
cp .env.example .env                     # paste your key into LLM_API_KEY

cd backend
python -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 7590

cd ../frontend                           # new terminal
npm install
npm run dev
```

Open <http://localhost:7591>. **There is no model server to start** — embeddings
and reranking run in-process; generation is a hosted API call.

> The first backend start downloads two small ONNX models (~150MB total) and
> applies database migrations. Subsequent starts take about two seconds.

</details>

### Ports

One contiguous block, chosen because it is unassigned in `/etc/services` and clear
of the crowded defaults (8000 is `irdmi`, 5432 is `postgresql`, and 5173 collides
with every other Vite project on the machine):

| Port | Service |
|---|---|
| **7590** | Backend API |
| **7591** | Frontend (Vite) |
| **7592** | Postgres (container-internal 5432) |
| **7593** | Qdrant (container-internal 6333) |

The dev server pins 7591 with `strictPort`, so a clash **fails loudly** instead of
drifting to the next free port — a silent drift leaves the UI talking to nothing
and looking merely empty.

## What you can do

| | |
|---|---|
| **Add filings** | Drag a **PDF or HTML** filing (`.pdf`, `.htm`, `.html`) onto the sidebar or use *Add filing*. Live status while it indexes. |
| **Ask questions** | Plain English. Answers carry an inline `p. N` chip for **every page the figure is reported on** — the statement, the MD&A discussion, a note — so you can check it wherever you prefer. |
| **Check the evidence** | Click any citation — now or from an old answer — and the source panel opens to that exact page with the quote shown, for PDF and HTML filings alike. Page numbers are the ones **printed on the page**, not our internal count, so they match the document in your hand. |
| **Organise** | Archive filings you're done with, or delete them. Delete is *soft*: the PDF and all history are kept and can be restored. |
| **Search** | `⌘K` / `Ctrl K` jumps to a filing or finds anything you've asked before. |
| **Copy answers** | Copies the answer together with its citation and quote, ready to paste into a note. |
| **Rate answers** | 👍/👎 on any answer, stored for later analysis. |

## Architecture

```
React + Vite ──HTTP──▶ FastAPI ──┬──▶ Postgres    (filings and history)      [Docker]
                                 ├──▶ Qdrant      (passage vectors)          [Docker]
                                 ├──▶ fastembed   (embeddings + reranking)   [in-process]
                                 ├──▶ finance/    (metric engine, fact store)[in-process]
                                 └──▶ hosted LLM  (prose answers)            [API]
```

**Nothing needs starting before the app except Docker.** This is deliberate: an
earlier version ran a local *generation* server, whose cold start took minutes on
CPU and was the single largest source of operational risk. Generation is now always
a hosted API call. The models that do run locally are the small ONNX embedding and
reranking models, which load in seconds.

### The two paths

A question is routed explicitly, and the answer records which path produced it —
a reader is entitled to know whether a figure was computed from tagged data or
read out of prose by a model, because those deserve different amounts of trust.

```
ingest   PDF or HTML → pages → passages (page-anchored, table-safe)
              → batch embed → Qdrant + BM25 + page cache
              → inline-XBRL facts (concept, value, period, page, segment axis)

ask      question
           │
           ├─ router: does this name a metric we can compute?
           │
           ├── YES ──▶ fact store → metric engine (arithmetic in code)
           │            └─ runs in parallel with the model, then adjudicates:
           │               agree → "corroborated" | differ → computed figure ships
           │
           └── NO ───▶ BM25 top-25 ─┐
                       vectors ─────┴→ rank fusion → cross-encoder rerank → 8 passages
                       → [+ statement pages the XBRL tags nominate]
                       → [+ pages of the 10-K Item the question names]
                       → [ambiguous? widen the pool and rerank again]
                       → [nothing relevant? stop here and decline]
                       → LLM (JSON-constrained, retried if the JSON is unreadable)
           │
           └──▶ verification gate (quote on page? numbers in quote or derivable?)
                → cited answer, every page it is reported on
                |  "not found in this filing"
```

Why each piece is there:

- **A financial ontology, not inference.** Analysts and filings use different words
  for the same quantity: a question asks for "capital expenditure"; the cash-flow
  statement says "Purchases of property, plant and equipment" and never contains
  the phrase asked for. `app/finance/ontology.py` is a curated table of concepts,
  US-GAAP tags, ~45 metric formulas and ~120 analyst phrasings — deliberately
  explicit, so a wrong mapping can be found and corrected rather than being an
  emergent property of a model.
- **The issuer's own tags.** Since the SEC's inline-XBRL phase-in, filings carry
  every reported figure twice: once as text, once as a tagged US-GAAP concept.
  That is authoritative rather than inferred. Older filings carry no tags, so their
  statements are located by how complete a page looks against the line items that
  statement is made of — measured at 0.81 points/question against 0.83 for tagged
  filings, so parsing is *not* the bottleneck.
- **Parallel execution with adjudication.** Both paths run; the verifier decides.
  Two independent derivations agreeing is stronger evidence than either alone, and
  the disagreement case is the one worth catching.

- **Hybrid retrieval.** BM25 catches exact financial vocabulary ("goodwill
  impairment", "SOFR"); vectors catch paraphrase. Neither alone is reliable.
- **Passage-level chunks with page anchors.** A whole 10-K page produces a badly
  diluted embedding; passages retrieve precisely while the page anchor keeps
  citations page-accurate.
- **Cross-encoder reranking.** Bi-encoders approximate relevance; a cross-encoder
  reads question and passage together. It's the largest single accuracy gain
  available, and its scores are *calibrated* — which is what makes the
  answer/decline threshold meaningful rather than a magic number.
- **Adaptive escalation.** Most questions come back decisively relevant or
  decisively not. Only in the ambiguous middle — where a wrong call costs a point
  either way — does it widen the candidate pool and rerank again.
- **Tables kept whole.** Financial tables are where the numbers live; splitting one
  separates a figure from its label.
- **Both source formats, neither converted.** EDGAR publishes HTML; most people
  archive PDFs. HTML pages are found from the `page-break-after` markers filing
  generators emit. Converting HTML to PDF would have reused one parser, but a
  different renderer paginates differently, moving every citation off the page the
  source actually used.

### Running on different hardware

The same build is expected on a thin laptop, a many-core server and a box with
a dedicated GPU, so the local embedding and reranking models (not the LLM, which
is hosted) are **sized from the machine at startup**
rather than to fixed constants (`backend/app/hardware.py`). `GET /health`
reports what was chosen, which is usually the fastest explanation for why two
machines running the same code differ in speed.

| Machine | ONNX threads | Batch | Runs on |
|---|---|---|---|
| 2 cores, 4GB | 1 | 16 | CPU |
| 4 cores, 8GB | 3 | 64 | CPU |
| 12 cores, 16GB | 9 | 64 | CPU |
| 32 cores, 64GB | 28 | 64 | CPU |
| 128 cores, 512GB | 124 | 64 | CPU |
| NVIDIA GPU | unrestricted | 256 | CUDA |
| Apple silicon | unrestricted | 256 | CoreML |

Two things drive this. **Cores are held back** because onnxruntime otherwise
claims every one, and on a laptop that makes the whole machine unresponsive
for the minutes an ingest runs — measured cost of reserving them is 1.4%.
The reservation is proportional at the small end and capped at four, so a
four-core laptop keeps a usable share and a 128-core server is not left
idling. **Batch size** was measured rather than guessed: on a 12-core laptop,
8 → 815ms/passage, 32 → 733, 64 → 707, 256 → 721, so the curve is flat past
64 and holding more in memory buys nothing.

**Acceleration is opt-in**, because the runtimes are ~1GB downloads and the
right one depends on the vendor — notably, **CUDA will not drive an AMD card**:

| Machine | Install | Provider used |
|---|---|---|
| **Apple silicon** | nothing | CoreML |
| NVIDIA, any OS | `pip install onnxruntime-gpu` | CUDA |
| **AMD or Intel on Windows** | `pip install onnxruntime-directml` | DirectML |
| AMD on Linux | ROCm wheel from AMD's index (not on PyPI) | ROCm/MIGraphX |
| Intel Mac, or no GPU | nothing to install | CPU |

See `backend/requirements-accelerate.txt` for the exact lines. Nothing in the
application changes — `hardware.py` notices the new provider has appeared and
routes both models to it. Getting this wrong is safe: a runtime whose provider
is unavailable simply is not used and the app falls back to the CPU. Both
bootstrap scripts check the installed adapters and say which package applies.

**Apple silicon is already accelerated** by the default install — CoreML is
compiled into the standard macOS wheel (confirmed present in the shipped
library), so it is detected and used with nothing extra. If CoreML turns out
slower than the CPU on a particular Mac, which is possible for a model this
small, set `FORCE_CPU=true`.

Every derived value can be pinned from `.env` when benchmarking, or when the
detected default is wrong for a particular box:

| Variable | Effect |
|---|---|
| `ONNX_THREADS` | Cores the models may use |
| `EMBED_BATCH_SIZE` | Passages per forward pass |
| `RERANK_BATCH_SIZE` | Documents per rerank pass |
| `FORCE_CPU` | Ignore a GPU that is present |

### Backend layout

```
backend/app/
  main.py          app factory + lifespan       config.py    settings
  exceptions.py    domain errors                domain/      models, enums
  hardware.py      sizes the local models to the machine
  db/              session, migrations, repositories/
  storage/         filing file store (PDF or HTML)
  ingestion/       parser (PDF), html_parser, chunker, embedder, pipeline,
                   xbrl_facts (inline-XBRL), page_labels (printed page numbers)
  finance/         ontology (concepts, metrics, aliases), fact_store,
                   metric_engine, multi_period, segment_engine
  retrieval/       bm25_index, fusion, reranker, retriever, qdrant_store,
                   fact_index (statement pages), section_index (10-K Items)
  qa/              router (which path), llm_client, prompts, verifier,
                   answer_service, chat_service
  api/             deps (DI), schemas (DTOs), errors, routes/
```

Repository pattern for data access, a service layer holding business logic, DI via
FastAPI `Depends`, DTOs kept separate from domain models, versioned SQL migrations,
and one central place mapping domain errors to HTTP status codes.

### Local state

Everything is local — no object storage, no cloud services.

```
storage/filings/{user_id|guest}/{filing_id}.{pdf,htm}  uploaded originals  (host)
storage/index/{filing_id}/passages.json           BM25 corpus            (host)
storage/index/{filing_id}/pages.json              page text, for the verifier
storage/index/{filing_id}/xbrl.json               concept -> pages, for retrieval
storage/index/{filing_id}/facts.json              tagged values, for the engine
data/postgres-backup/                             pg_dump target         (host)
plutus_postgres                                   Postgres data   (docker volume)
plutus_qdrant                                     vector storage  (docker volume)
```

**Both datastores use Docker named volumes rather than host folders, and that is
a portability requirement rather than a preference.** Neither engine survives a
Windows bind mount. Postgres refuses to start unless its data directory is
`0700`, which a Windows mount cannot express — `chmod` there is a silent no-op.
Qdrant *starts*, which is worse, then stops completing writes: measured back to
back on one host, creating an empty collection took **0.07s** on a named volume
and **never returned** on a bind mount, while reads stayed at 3ms. Named volumes
behave identically on Windows, macOS and Linux.

**The backup set is a `pg_dump` plus `storage/filings/`.** Everything else is
derived — `storage/index/` and the vector store both rebuild by re-ingesting the
originals. Dump the database into a host folder with:

```bash
docker compose exec postgres pg_dump -U analyst analyst_copilot > data/postgres-backup/dump.sql
```

Deleting a filing sets `deleted_at`. Nothing is removed from disk, so a mistaken
delete is recoverable and any citation already shown still resolves.

## Configuration

All settings live in `.env` (see `.env.example`). The ones worth knowing:

| Variable | Default | Notes |
|---|---|---|
| `LLM_API_KEY` | — | **Required.** Key for the configured endpoint. |
| `LLM_MODEL` | `minimax/minimax-m3:free` | OpenRouter's **free** tier of MiniMax M3 — what the published figures were measured on. Any model on the configured endpoint works; a paid frontier model is the biggest available accuracy gain, see above. |
| `LLM_BASE_URL` | OpenRouter | Any OpenAI-compatible endpoint (OpenRouter, Groq, Gemini, Together…). |
| `USE_METRIC_ENGINE` | `true` | Compute named metrics in code instead of asking the model. Turning it off is a measurable accuracy loss. |
| `RELEVANCE_THRESHOLD` | `-2.0` | Raise to decline more readily, lower to answer more readily. |
| `ENRICH_FILINGS` | `true` | Company/period extraction + suggested questions. Costs two extra API calls per upload. |
| `JWT_SECRET` | dev value | **Change for any non-local deployment.** |

If the provider's usage limit is hit, short bursts are retried automatically and a
genuine quota exhaustion is reported in the UI as *"AI usage limit reached"* with
guidance, rather than a generic failure.

## Known limitations

- **No OCR.** Scanned pages with no text layer extract as empty. Every SEC EDGAR
  filing we expect is digitally native, but a scanned exhibit would be missed.
- **HTML without page-break markers becomes one page.** A handful of filings — mostly
  8-Ks — carry no pagination at all, so the whole document is page 1. The citation is
  still honest, just less precise.
- **Custom formulas are refused, not computed.** If a question spells out a
  definition the ontology does not model — "ROE defined as net income / (equity
  less goodwill)" — the system declines rather than substituting its own formula.
  That is deliberate (answering the wrong definition costs −1, refusing costs 0),
  but an analyst with house definitions is not served yet. A small expression
  evaluator over ontology concepts is the natural next step.
- **The model cannot ask for clarification.** Generation is a single call: it
  answers or reports "not found". A question missing a fiscal year is answered for
  the most recent year in the filing rather than queried.
- **Prose answers cannot be numerically verified.** "Boeing's business is
  cyclical" contains no figure to check against a quote, so judgement questions
  rest on retrieval quality and the model, not on the verification gate. This is
  where a paid model pays for itself.
- **Free-tier providers are non-deterministic.** Two identical runs differed on 16
  of 136 questions. Computed figures are unaffected; prose answers are not.
- **Existing filings do not pick up pipeline improvements.** Uploads are
  de-duplicated by content hash, so a filing indexed before an ingestion change
  keeps its old index. Delete and re-add it to re-index.
