# Retrieval evaluation

A deterministic measurement of the retrieval layer behind `search`. The
lexical and BM25 rows call no model and open no network connection; the vector
and hybrid rows call the configured local embedding service. It is a
measurement harness, not a CI pass/fail gate.

## Dataset

The set is local: `.scratch/` is not committed. A run reads
`.scratch/rag/dataset/queries.jsonl`, one JSON object per line:

| Field | Meaning |
|---|---|
| `id` | Optional stable name; a missing id is derived from the line number (`q01`, …; blank lines are skipped) |
| `query` | The words a reader would type |
| `type` | `exact`, `paraphrase`, `cross_page`, `gap`, or `notes` |
| `expected_pages` | Wiki slugs that answer the query (empty for `gap` and `notes`) |
| `expected_notes` | Note ids that answer a `notes` query (empty otherwise) |
| `rationale` | Optional note explaining the annotation |

The full set has 20–30 queries and every type. The corpus is the sibling
`.scratch/rag/dataset/corpus/` project (`wiki/` and `notes/`). Before a query
runs, every expected page and note is checked against that corpus, so a typo
fails loudly instead of scoring zero.

## Running it

```bash
python -m evals.rag
python -m evals.rag --variant lexical
python -m evals.rag --variant hybrid --scope wiki \
  --embedding-endpoint http://127.0.0.1:11434/v1 \
  --embedding-model qwen3-embedding:0.6b --embedding-dim 1024
python -m evals.rag --matrix channel \
  --dataset .scratch/rag/prototype/queries.jsonl \
  --corpus .scratch/rag/prototype \
  --embedding-endpoint http://127.0.0.1:11434/v1 \
  --embedding-model qwen3-embedding:0.6b --embedding-dim 1024
python -m evals.rag --dataset path/to/queries.jsonl --corpus path/to/project \
  --report build/evals/rag/report.json
```

- `--variant` defaults to `lexical`: the project's local `rank_text` search
  (4/3/2/1 over slug, title and body) over wiki pages and, in the experiment
  scope, notes. The other variants are `bm25`, `vector` and `hybrid`; they need
  an embedding endpoint.
- `--scope` is `wiki+notes` by default; `wiki` drops note results and the six
  notes queries, so the main table is the production scope (n=34 for the
  10-page prototype). The report records the scope it ran under.
- `--matrix channel` runs `lexical`, `bm25`, `vector` and `hybrid` in both
  scopes and writes one report with a main (wiki-only) and a secondary
  (wiki+notes) table. One base index is built and reused across the eight runs.
- `--embedding-endpoint` / `--embedding-model` / `--embedding-dim` /
  `--embedding-api-key` configure the OpenAI-compatible embedding service. The
  endpoint and model can also come from the `NOTEAGENT_RAG_EMBEDDING_*`
  environment variables.
- `--dataset` defaults to `.scratch/rag/dataset/queries.jsonl` (the old
  25-query smoke set); `--corpus` defaults to its sibling `corpus/`. The
  10-page channel corpus is local at `.scratch/rag/prototype/`; pass it
  explicitly for the matrix.
- `--report` writes JSON and a Markdown summary beside it. The default is
  `build/evals/rag/report.json`, a build artifact.

## Reading the report

The report contains Recall@5, Hit@5, nDCG@10 and MRR over the scored queries,
p50/p95 per-query latency, the per-type buckets, gap abstention and every
query's ranked result. `gap` queries are not scored with recall (there is
nothing to find); they report whether the system abstained instead. The report
also records the scope and, for variants that build an index, one row per
index build (chunking, model, dimensions, build seconds, embedding calls and
tokens, chunk count, size). Build cost is separate from query latency.

The channel matrix report keeps the same metric block, adds p50/p95 per row,
and has one index-build table for the builds the eight runs shared.

The lexical baseline on the old 25-query set: Recall@5 0.800, Hit@5 0.800,
nDCG@10 0.722, MRR 0.683; on this development machine, p50 about 5 ms and
p95 about 6 ms; 5/5 gap queries abstained. The paraphrase bucket is the weak
spot (0.333), which is what a hybrid retriever is meant to improve. The local
10-page prototype and the channel matrix are the Stage 2 baseline; they stay
out of git, so their numbers are in the generated matrix report.

## Variants

- `lexical` — the historical `rank_text` search; no index.
- `bm25` — BM25F over heading chunks with the exact-title tier. It still builds
  the vector index, because the chunk table holds the embeddings, and reports
  that build cost.
- `vector` — Qwen3 chunk cosine with the reference page aggregation and an
  independent exact-title check.
- `hybrid` — BM25F + vector chunks fused by RRF k=60 with the exact tier; the
  reference pipeline. `hybrid+rerank` waits for a rerank service.

A variant prepares a retriever once from an eval context, then answers one
query at a time. It declares an optional index key; the harness caches indexes
by that key and records each build's cost separately from query latency. The
prepared retriever returns ranked page slugs and note ids, so the dataset and
report stay the same.
