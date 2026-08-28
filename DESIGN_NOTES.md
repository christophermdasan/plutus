# Design Notes — The Analyst Copilot

## The actual problem

An analyst acts on what this system says. A fabricated figure propagates into a
model, a memo, a recommendation, and is expensive to catch downstream. A gap is
cheap by comparison — someone opens the filing and reads it.

Accuracy here is therefore asymmetric, and the scoring policy the pipeline is
measured against is built to match: a correct cited answer earns +1, an honest
"not found" earns 0, a correct answer citing the wrong page earns 0, and a
confident wrong answer costs −1. A system that guesses finishes below zero; one
that never answers finishes at exactly zero. The engineering problem isn't
"answer well" — it's *know the difference between an answer you can prove and one
you can't*, and be willing to say nothing.

Everything below follows from that.

## The design that survived every rewrite

**A programmatic verification gate.** The model proposes an answer in a JSON schema
that forces it to name a page and a verbatim quote. Then, before anything reaches
the user, the system checks the quote genuinely appears on that page and that any
number in the answer genuinely appears in that quote. Either check failing turns the
answer into a refusal, regardless of how confident the model sounded.

Schema-constrained generation only guarantees the *shape* of a citation. Checking it
against the source is what makes it true. That distinction is the product.

**Page-anchored citations.** Retrieval units always carry the page they came from,
so a citation resolves to a real page rather than an arbitrary chunk boundary.

**Hybrid retrieval.** BM25 for exact financial vocabulary, vectors for paraphrase,
fused by reciprocal rank fusion. Neither signal alone was reliable enough to gate an
LLM call on.

## What we measured

Scored on 19 hand-written questions with known page/quote ground truth, run end to
end through the production code path — real parsing, retrieval, reranking,
generation and verification:

| Configuration | Score | Correct page | Wrong page | Abstained | Wrong |
|---|---|---|---|---|---|
| Local 8B model, page-chunks, no reranker | +9/19 | 10 | 2 | 6 | 1 |
| Hosted model, same old pipeline | +7/19 | 8 | 0 | 10 | 1 |
| Hosted model, rebuilt RAG pipeline | +9/19 | 10 | 1 | 7 | 1 |
| …plus the numeric-verification fix below | +10/19 | 11 | 1 | 6 | 1 |
| **…plus the benchmarked reranker upgrade** | **+11/19** | **12** | **1** | **5** | **1** |

The middle row is the interesting one: simply swapping in a much stronger model
made the score *worse*. It was more conservative in its phrasing, so more of its
answers failed the verbatim-quote check and became refusals. That is the system
behaving correctly — but it showed the pipeline, not the model, was the binding
constraint. Rebuilding retrieval recovered those two points, and fixing the
verification bug below recovered another. Median latency is ~3.2s.

Zero hallucinations on genuinely unanswerable questions in every configuration.

## Bugs found by running things, not by reading them

1. **BM25's IDF goes negative** when a term appears in more than half a small
   corpus, ranking a passage *without* the query term above one containing it.
   Floored the IDF; added a regression test reproducing the exact pathology.
2. **Borderless tables were invisible.** pdfplumber's default requires three
   aligned words to call something a column, which silently skipped short summary
   tables — exactly the `Item | 2023 | 2022` tables carrying headline figures.
   Found by inspecting word coordinates after a test failed.
3. **Reasoning models silently truncate.** A token cap tuned for a plain instruct
   model left a reasoning model no budget for its answer after thinking, producing
   `json_validate_failed`. Only visible because the error body was surfaced —
   `raise_for_status()` alone reports "400 Bad Request" and hides the reason.
4. **A connection pool that opens lazily.** `open=True` returns immediately and
   opens in the background, so the *first real request* silently absorbed several
   seconds. Now opened synchronously at startup.
5. **Uploading the same file twice created two filings.** Content-hash dedup fixed
   it — and the test that caught it was itself wrong first, because the PDF
   generator stamps a timestamp, so "the same file" wasn't.
6. **The verifier rejected correct multi-year answers.** Reported from real use:
   *"revenue in FY2023"* worked, but *"revenue in FY2023 and FY2022"* returned "not
   found" despite both figures being in the filing. The numeric check extracted
   `2023`/`2022` out of "FY2023"/"FY2022" and demanded they appear in the quoted
   table row — but years live in the table *header*, not the data row. A correct
   answer was being thrown away. Fixed by distinguishing figures the answer
   *asserts* from numbers it merely echoes from the question, plus narrowly-scoped
   leniency for years that label an already-verified figure. Worth +1 on the eval,
   and the strict rule for money is unchanged.

## The largest lesson: model *load* time, not inference time

A chat request hung for minutes. Reading the model server's own logs, rather than
guessing, showed three compounding causes — none of them in our code:

- CPU inference requires a one-time tensor **repack** on load, taking *minutes*,
  not seconds. Nothing was slow; the model simply wasn't ready.
- **Closing the connection early aborts the load server-side.** Every impatient
  retry made it worse by forcing the next attempt to start over.
- Killing the model server's parent process orphans its per-model children, so
  several competing inference processes accumulated invisibly.

We fixed all of it (keep-alive, generation caps, explicit timeouts, background
warm-up) and it worked. Then we removed the entire dependency instead.

That's the decision worth defending: **the fix was correct and we still deleted the
thing it fixed.** A deployment that requires a background service to be warm, in the
right state, and started in the right order is fragile in a way no amount of careful
handling repairs. What replaced it:

- **Embeddings and reranking moved in-process** (fastembed/ONNX). Faster than the
  server it replaced (~21ms vs ~80ms per passage, no HTTP hop), with no cold-start
  cliff and nothing to start.
- **Generation moved to a hosted API.** ~1s instead of minutes, and the whole class
  of local-inference failure modes disappeared.

`docker compose up` and two dev servers is now the entire runbook.

## What we added because the architecture allowed it

Once answers took a second instead of minutes, things that were previously
impractical became obvious:

- **Filing metadata extraction** — the sidebar reads "MERIDIAN ROBOTICS · 10-K ·
  FY2023" instead of a filename.
- **Suggested questions per filing**, generated from its opening pages.
- **Explainable refusals** — "show what was checked" lists the passages considered
  and their relevance scores. This one matters most: it turns the product's
  willingness to say "no" from a limitation into visible evidence of rigour.
- Command palette, per-answer feedback, copy-with-citation, archive/soft delete,
  optional accounts with per-user history.

## What we deliberately did not build

- **A model picker in the UI.** Which model answers is an operator decision in
  config, not a per-session user choice. Users need to know it *works* — hence a
  connection test that asks a real question and checks the answer, rather than
  pinging an endpoint.
- **A full async rewrite.** FastAPI already gives real thread concurrency for
  I/O-bound work; a rewrite was risk without measurable benefit at this scale.
- **Multi-hop retrieval.** Adaptive escalation handles the ambiguous cases more
  cheaply. This is the first thing to revisit if production questions need genuine
  cross-page synthesis.
- **LlamaParse / Cohere Rerank / LlamaIndex**, recommended by an external review.
  The first two are hosted commercial APIs, so their open local equivalents were
  benchmarked instead (`docs/BENCHMARKS.md`). `unstructured` found *zero* tables in
  its dependency-free mode and needs a system Tesseract install otherwise;
  bge-reranker scored a genuinely answerable question *below* the abstain
  threshold, so adopting it would have caused wrong refusals. The review's
  reranking instinct was right, though — benchmarking it led to a larger ms-marco
  model that is worth +1. LlamaIndex would wrap what already exists without adding
  accuracy.
- **Hard deletes.** Every delete is soft. Losing a document to a mis-click is worse
  than keeping a row too long.

## What we'd do next

Calibrate `relevance_threshold` against a larger labelled set — 19 questions catch
pipeline bugs but can't tune a threshold with statistical confidence. Then
multi-citation answers (the schema and the citation UI both generalise to it),
OCR fallback for scanned pages, and streaming, which the latency now makes
worthwhile.
