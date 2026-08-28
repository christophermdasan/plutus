# Component benchmarks

Measurements behind the component choices, so they can be re-checked rather
than taken on faith. All run locally against the bundled sample filings.

## Reranker selection

Prompted by an external review recommending Cohere Rerank. Cohere's reranker
is an API-only commercial product, so the open, locally-runnable equivalents
were tested instead.

### Ranking accuracy — does it put the right passage first?

Eight financial questions against a 12-passage filing:

| Model | Top-1 correct | Latency/query | Size |
|---|---|---|---|
| `ms-marco-MiniLM-L-6-v2` | 8/8 | 398 ms | 0.08 GB |
| `ms-marco-MiniLM-L-12-v2` | 8/8 | 784 ms | 0.12 GB |
| `BAAI/bge-reranker-base` | 8/8 | 2083 ms | 1.04 GB |
| `jina-reranker-v1-turbo-en` | 8/8 | 518 ms | 0.15 GB |

All four rank correctly. Ranking alone does not discriminate between them.

### Threshold separation — the measurement that actually matters

This system doesn't only *rank*; it **thresholds**, deciding whether there is
anything worth answering from at all. So the useful question is how far apart
it scores answerable and unanswerable questions.

Four answerable questions vs four genuinely absent from the filing:

| Model | Worst answerable | Best unanswerable | Margin |
|---|---|---|---|
| `ms-marco-MiniLM-L-6-v2` | **+2.70** | −6.74 | +9.44 |
| **`ms-marco-MiniLM-L-12-v2`** | **+4.80** | −6.14 | **+10.94** |
| `BAAI/bge-reranker-base` | **−1.92** ⚠️ | −5.28 | +3.36 |
| `jina-reranker-v1-turbo-en` | **−0.39** ⚠️ | −1.19 | +0.81 |

**bge-reranker-base scores a genuinely answerable question at −1.92 — below
the abstain threshold of 0.** Adopting it would make the system refuse
questions it can currently answer. Jina's margin (+0.81) is far too narrow to
set a stable threshold against.

The cause is score *range*, not quality: bge and jina emit compressed,
normalised scores, while the ms-marco cross-encoders emit raw logits with wide
dynamic range. Compression is harmless for ordering and harmful for
thresholding.

### End-to-end, on the scoring policy

Back-to-back eval runs, same conditions:

| Reranker | Score | Correctly cited | Abstained |
|---|---|---|---|
| L-6 | +10/19 | 11 | 6 |
| **L-12** | **+11/19** | **12** | 5 |

One question changed, and the change is explainable rather than noise:

> **q07 — "What is the maturity date of the term loan facility?"**
> L-6 scored the relevant passage below threshold and wrongly abstained,
> forfeiting a point. L-12 scored it above threshold and answered correctly
> from page 7.

That is exactly what the higher "worst answerable" score predicts.

**Adopted at the time: `ms-marco-MiniLM-L-12-v2`** — +1 on the scoring policy
for ~390 ms.

> **Superseded.** This comparison was run on the 19 synthetic questions, which
> reuse each filing's own wording. That flatters lexical matchers and, as it
> turned out, inverted the ranking: re-measured on the 136 real analyst
> questions, `jina-reranker-v1-turbo-en` beats both ms-marco models at every
> operating point, and `bge-reranker-base` — rejected here for a compressed
> score range — is simply weaker rather than differently scaled. See
> *Choosing the reranker on real questions* below. The lesson is about the
> benchmark, not the models: a synthetic set built by paraphrasing a filing
> cannot rank retrievers for questions phrased by analysts.

## PDF parsing

Prompted by the same review recommending LlamaParse. LlamaParse is a hosted
paid service (documents leave the machine), so `unstructured` — the
open-source local equivalent — was tested instead.

| Parser | Tables detected | Time | System dependencies |
|---|---|---|---|
| **Ours** (PyMuPDF + pdfplumber) | **8** | **0.2 s** | none |
| `unstructured` (`strategy="fast"`) | 0 | 4.1 s | none |
| `unstructured` (`strategy="hi_res"`) | — | — | **Tesseract binary** |

`fast` performs no table inference at all. `hi_res` does, but requires
Tesseract installed as an operating-system package on every machine — which
would give back the "install this before the app will work" property that
removing the local model server was meant to eliminate.

Our parser already extracts these tables correctly, including borderless
ones, and serialises rows so a label stays beside its figure:

```
Warehouse Automation | $139.0 | $118.7
```

**Kept our parser.** Revisit `unstructured` + Tesseract only if scanned
(non-text-layer) filings become a requirement — that is the one case where it
would genuinely win, since it brings OCR.

## LlamaIndex

Open source and local, so it meets the cost and privacy bar — but it is a
framework, not a capability. It would wrap the retrieval, fusion and reranking
this project already implements directly, adding an abstraction layer without
adding accuracy, and obscuring the verification gate that differentiates the
system.

**Not adopted**, on grounds of value rather than cost.

## Reproducing

```bash
cd backend
python scripts/eval.py                                    # current config
RERANKER_MODEL="Xenova/ms-marco-MiniLM-L-6-v2" python scripts/eval.py   # compare
```

---

# Real-filing validation

Everything above was measured on synthetic filings. This section covers
testing against genuine SEC 10-Ks (Apple FY2025, NVIDIA FY2026), which
exposed problems the synthetic set could not.

## First: the source PDFs were unusable

The filings first collected into `reference/` could not be read by *any*
text-based tool:

| File | Pages | Pages with text | Extractable characters |
|---|---|---|---|
| Apple.pdf | 109 | **0** | **0** |
| Microsoft.pdf | 144 | **0** | **0** |
| NVIDIA.pdf | 113 | **0** | **0** |
| McDonalds.pdf / Tesla.pdf | 0 | – | corrupt |
| Ford.pdf | – | – | 0 bytes |

Diagnosis: `producer: "Microsoft: Print To PDF"`, and per page **0 fonts, 0
images, 242 vector drawings**. They were produced by printing a web page to
PDF, which converts glyphs into vector outlines. The content is visually
perfect and completely absent as text.

This is a **document acquisition** problem, not a parser problem. No parser
recovers text that was destroyed at creation - not ours, not LlamaParse, not
`unstructured` without OCR.

**Fix:** fetch the authoritative documents from SEC EDGAR and render them
with a real text layer. `scripts/fetch_reference_filings.py` does this.

## Parser performance on genuine 10-Ks

| Filing | Pages | Parse | Passages | Tables found |
|---|---|---|---|---|
| Apple FY2025 | 82 | 8.9 s | 149 | 84 |
| McDonald's FY2025 | 100 | 21.6 s | 187 | 83 |
| NVIDIA FY2026 | 125 | 16.2 s | 238 | 104 |
| Tesla FY2025 | 167 | 26.8 s | 301 | 142 |

Comfortably inside the pipeline's ten-minute ingestion budget, and table extraction
holds up on real filings - a genuine Apple segment row comes through as:

```
Total net sales | $ | 416,161 |  | 6 | % |  | $ | 391,035 |  | 2 | % |  | $ | 383,285
```

## Two real bugs the synthetic set never surfaced

Both were **false negatives**: the model produced a correct, genuinely
supported answer and the verifier threw it away.

### 1. Currency symbols occupy their own table cell

Real SEC tables place `$` in a separate column, so a row serialises as
`Total net sales | $ | 416,161`. The model quotes the natural `$416,161`,
which is not a literal substring. Correct answers were rejected.

Sometimes the reverse: the symbol is hoisted into the column header, leaving
a bare `64,377` in the cell while the model writes `$64,377`.

**Fix:** quote matching now normalises *formatting only*, in three stages -
whitespace/case, then table cell delimiters, then currency symbols. The
invariant is unchanged and explicitly tested: **the digits of any claimed
figure must still appear, in order, on the cited page.** Five tests assert
that fabricated figures are still rejected after each relaxation.

### 2. The context window was one passage too narrow

A large filing repeats similar tables across MD&A, the statements and the
notes. Asked for Greater China net sales, the specific segment table ranked
**6th** while the window sent only the top **5** - so the answer was refused
despite being retrieved.

**Fix:** `context_passages` 5 → 8.

### Result

| Question (Apple FY2025 10-K) | Before | After |
|---|---|---|
| Total net sales in fiscal 2025 | refused | **$416,161 M**, p.69 |
| Greater China net sales | refused | **$64,377 M**, p.32 |
| Who is the CEO | Timothy D. Cook, p.81 | unchanged |
| CEO total compensation | refused | refused (correct - that is in the DEF 14A, not the 10-K) |

## Free-tier capacity is a real constraint

Groq's free tier allows **200,000 tokens/day** and **8,000 tokens/minute**
per model. Real 10-K context is far larger than synthetic filings, so:

- a single question costs roughly 3-15k tokens
- that is on the order of **15-60 real questions per day per model**
- quotas are per-model, so switching between `gpt-oss-120b` and
  `gpt-oss-20b` effectively doubles the budget

The application reports exhaustion accurately ("AI usage limit reached",
with a daily-vs-burst hint), and retries short bursts automatically. Any
sustained analyst workload needs a paid tier or a self-hosted endpoint; the
free tier is sized for evaluation, not for production use.

This also makes the full test suite flaky when run end to end: several tests
make real API calls in quick succession and trip the per-minute limit. They
pass individually. Deselect them with `-k "not real"` or pace the run.

---

# Performance and accuracy on the FinanceBench corpus

Measured against the supplied data pack (78 SEC filings, 136 questions with
page-level ground truth). Everything below is from this machine unless a
different tier is named.

**Test machine.** Intel i7-1255U (2 performance + 8 efficiency cores, 12
threads, 15W), 16GB RAM, no GPU, Windows 11. A thin laptop - deliberately the
low end of the hardware this is expected to run on.

## Ingestion

Wall clock from upload accepted to `ready`, via the HTTP API.

| Filing | Size | Pages | Passages | Total |
|---|---|---|---|---|
| Apple FY2025 (PDF) | 0.6 MB | 82 | 149 | 150 s |
| AMD 2022 10-K (HTML) | 2.6 MB | 108 | 683 | **375 s** |
| Adobe 2022 10-K (HTML) | 3.0 MB | 99 | 653 | 819 s |
| 3M 2018 10-K (HTML) | 9.6 MB | 134 | 858 | ~900 s |

Broken down, on Adobe:

| Stage | Time | Share |
|---|---|---|
| parse | 0.9 s | 0.2% |
| chunk | 0.0 s | — |
| **embed** | **407.9 s** | **99.8%** |

Ingestion is embedding. Parsing and chunking are free by comparison, so
every ingestion-speed question is a question about embedding throughput.

## Embedding throughput

Cost scales with sequence length, not per-call overhead:

| Passage | Time each |
|---|---|
| 32 chars (~8 tokens) | 16.7 ms |
| 1,900 chars (~475 tokens) | 880 ms |

Roughly **540 tokens/sec** on this CPU. A 100-page 10-K is ~115k tokens, so
several minutes is the floor here, not a defect.

**Threads.** onnxruntime claims every core by default, which makes the machine
unusable while an ingest runs. Reserving cores costs almost nothing:

| Threads | 20 passages | ms each |
|---|---|---|
| all (12) | 17.2 s | 859 |
| **8 (4 reserved)** | 17.4 s | **871** |

1.4% slower for a third of the CPU back. Fewer threads is worse, not better -
1 thread costs 1,473 ms/passage against 794 for all twelve - so this is
headroom, not throttling.

**Batch size**, measured rather than guessed:

| Batch | ms/passage |
|---|---|
| 8 | 815 |
| 32 | 733 |
| **64** | **707** |
| 256 | 721 |

Flat past 64, so holding more in memory buys nothing on CPU. GPU profiles use
256, where a large batch is what keeps the device busy.

## Does the machine stay usable?

A fixed unit of arithmetic, timed repeatedly during a full AMD ingest. Its
duration is the honest signal: if the machine is saturated or swapping, the
same work takes longer.

| | |
|---|---|
| Probe at idle | 13.7 ms |
| **Median during ingest** | **18.4 ms (1.34x)** |
| Worst during ingest | 40.8 ms (2.98x) |
| Free RAM low-water | 2.22 GB |

A 34% slowdown is barely perceptible. For contrast, before the core
reservation and the pdfplumber fix, ingesting a large PDF drove free memory to
**0.3 GB** with 4.5 GB of pagefile in use, and CPU fell to ~8% because the
machine was swapping rather than computing.

## Query latency

| Configuration | Median |
|---|---|
| rerank 12 candidates | 6.3 s |
| rerank 50 candidates | ~9 s |
| rerank 50 + escalation to 150 | **24 s** |

Reranking is 2.0 s at 50 candidates and 6.2 s at 150. The escalation band is
`ESCALATE_ABOVE < score < ESCALATE_BELOW`; with the relevance threshold at
-6.0 - the same value as `ESCALATE_ABOVE` - nearly every answerable question
falls inside it and reranks three times as many candidates. The current
configuration is tuned for accuracy, and that is where its latency went.

## Accuracy: three defects, each measured

### 1. Fusion discarded the answer before it could be judged

Whether the ground-truth page reaches the reranker at all, on the seven AMD
questions:

| Stage | Ground-truth page present |
|---|---|
| BM25, top 25 | 5/7 |
| Vectors, top 25 | 4/7 |
| **Fused, cut to 12** | **1/7** |
| Fused, top 50 | **6/7** |

Both retrievers were finding the answer; `rerank_candidates = 12` threw it
away. Now 50.

### 2. The relevance threshold refused most correct evidence

Every one of the 136 questions scored against the evidence FinanceBench says
proves it, and against real filing prose that does not:

| | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| Passages that truly answer | -11.14 | -9.59 | -2.82 | +2.95 | +9.67 |
| Passages that do not | -11.24 | -10.97 | -8.91 | -4.59 | +0.80 |

| Threshold | True answers admitted | Recall | Precision |
|---|---|---|---|
| **0.0 (was)** | 37/136 | **27%** | 92% |
| -4.0 | 81/136 | 60% | 89% |
| **-6.0 (now)** | 100/136 | 74% | 79% |
| -8.0 | 110/136 | 81% | 70% |

At 0.0 the system could not exceed 27% however good the rest of it was. The
distributions overlap heavily, which is the real finding: the cross-encoder
separates these questions poorly.

### 3. The verifier rejected correct answers on punctuation

`[\d,]*` treated a clause comma as a thousands separator:

```
answer: "...December 31, 2022, with sales"  ->  token "2022,"
quote:  "...December 31, 2022. Sales to"    ->  token "2022"
```

An answer quoting its source verbatim was refused for containing a figure its
own quote supposedly lacked. A comma now only counts when three digits follow.

**Effect of all three, on the AMD 10-K: 1/7 answered -> 3/7.**

## The limit that is not a bug

The cross-encoder scores the exact sentence answering *"Did AMD report
customer concentration in FY22?"* -

> One customer accounted for 16% of our consolidated net revenue for the year
> ended December 31, 2022.

- at **-9.52**. `ms-marco-MiniLM-L-12-v2` is trained on web-search relevance
and has never been taught that "customer concentration" means "one customer
accounted for 16% of revenue". This is a domain-vocabulary gap, and it is the
single largest remaining source of refusals.

Separately, roughly a third of FinanceBench is **not extractive**: a quick
ratio must be computed from four line items and appears nowhere in the filing.
The verification gate refuses exactly that by design, which is what keeps the
system out of negative-scoring territory. No amount of retrieval tuning
reaches those questions; they need a numeric-reasoning path that shows its
arithmetic from verified line items.

## Hardware

| Tier | Spec | 100-page filing | Verdict |
|---|---|---|---|
| Minimum | 4 cores, 8 GB, SSD | ~15-20 min | Works, painful |
| **Recommended** | 8+ cores, 16 GB | **~6 min** | Usable |
| **Best value** | NVIDIA GPU >=6 GB VRAM, 8 cores, 16 GB | **~20-40 s** | The upgrade that matters |
| Apple silicon | M1/M2/M3, 16 GB | ~3-5 min | CoreML, automatic |

RAM beyond 16 GB does nothing: peak backend usage is ~1.6 GB once the
pdfplumber page caches are released.

### What hardware does not change

The same query, same model, different core allocations:

| Threads | Time | Reranker scores |
|---|---|---|
| 2 | 0.04 s | -9.5227, -11.2124, -11.1427 |
| 4 | 0.03 s | -9.5227, -11.2124, -11.1427 |
| 9 | 0.02 s | -9.5227, -11.2124, -11.1427 |
| all | 0.02 s | -9.5227, -11.2124, -11.1427 |

Identical to four decimal places. **Hardware changes how fast an answer
arrives, never what it is.** A GPU makes a correct answer arrive in seconds
and a wrong refusal arrive just as quickly. Accuracy is a function of the
models, the thresholds and the chunking - none of which a faster machine
touches.

Answer latency is also largely hardware-independent: generation is a hosted
API call, so only retrieval and reranking are local.

## Reproducing

```bash
cd backend
python scripts/perf_probe.py \
  --file ../data/sample/analyst-copilot-data/filings/AMD_2022_10K.htm \
  --questions ../data/sample/analyst-copilot-data/practice-questions.jsonl \
  --doc-name AMD_2022_10K
```

Reports ingestion timing by phase, a responsiveness probe against an idle
baseline, and per-question latency with the cited page against ground truth.


## Choosing the reranker on real questions

Each of the 136 supplied questions scored against the evidence that answers it
and against filing prose that does not. AUC is the chance a true passage
outranks a false one; the rest is the recall still available at a given
precision.

| Model | AUC | r@95% | r@90% | r@85% | r@80% | ms/pair | Size |
|---|---|---|---|---|---|---|---|
| ms-marco-MiniLM-L-12-v2 | 89% | 23% | 60% | 68% | 72% | 132 | 0.12 GB |
| ms-marco-MiniLM-L-6-v2 | 91% | 50% | 65% | 72% | 77% | 58 | 0.08 GB |
| **jina-reranker-v1-turbo-en** | **94%** | **62%** | **74%** | **82%** | **85%** | 94 | 0.15 GB |
| bge-reranker-base | — | — | 56% | — | — | 349 | 1.04 GB |

**Adopted: `jina-reranker-v1-turbo-en`.** Better at every operating point,
faster than the model it replaces, and an eighth the size of the largest
candidate. At 95% precision it retains 62% of true answers where ms-marco-L-12
retains 23%.

Its score scale differs, so the thresholds move with it:

| Threshold | True answers admitted | Precision |
|---|---|---|
| -3.0 | 94% | 64% |
| **-2.0 (adopted)** | **86%** | **76%** |
| -1.31 | 74% | 90% |
| 0.0 | 29% | 100% |

-2.0 rather than the precision-maximising point because the two errors are not
symmetric: an irrelevant passage that reaches the model is nearly always
refused downstream by the verification gate and scores 0, while a true answer
stopped at this threshold can never be recovered. The verifier is the guard;
this number only decides what gets looked at.

`escalate_above`/`escalate_below` moved with it, and moved into settings -
expressed on the reranker's scale, they meant something different the moment
the model changed. Sitting the lower bound exactly on the threshold sent
almost every answerable question through a second, larger rerank and took
median latency from 6s to 24s.

**Measured end to end on the AMD 10-K:** 3/7 answered at a median of **6.1s**,
against 3/7 at **24.3s** with ms-marco and the wider escalation band - same
accuracy on this sample, four times faster. Seven questions cannot resolve a
14-point recall difference; the 136-question table above is the evidence for
the switch, and a full run is what should confirm it.
