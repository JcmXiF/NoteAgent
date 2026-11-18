# Retrieval evaluation

An offline, deterministic measurement of the retrieval layer behind `search`.
It calls no model and opens no network connection, and it is a measurement
harness, not a CI pass/fail gate.

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
python -m evals.rag --scope wiki
python -m evals.rag --dataset path/to/queries.jsonl --corpus path/to/project \
  --report build/evals/rag/report.json
```

- `--variant` defaults to `lexical`: the project's local `rank_text` search
  (4/3/2/1 over slug, title and body) over wiki pages and, in the experiment
  scope, notes. Page ranking matches the `search` tool; notes are scored in
  their own scope.
- `--scope` is `wiki+notes` by default; `wiki` drops note results from every
  variant. The report records the scope it ran under.
- `--dataset` defaults to `.scratch/rag/dataset/queries.jsonl`; `--corpus`
  defaults to its sibling `corpus/`.
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

The lexical baseline on the 25-query set: Recall@5 0.800, Hit@5 0.800,
nDCG@10 0.722, MRR 0.683; on this development machine, p50 about 5 ms and
p95 about 6 ms; 5/5 gap queries abstained. The paraphrase bucket is the weak
spot (0.333), which is what a hybrid retriever is meant to improve.

## Variants

Only `lexical` is wired into this harness today. The BM25, vector and hybrid
modules exist behind `search`, but wiring them into this harness is
outstanding; they will share the same dataset and report. A variant prepares a
retriever once from an eval context, then answers one query at a time. It
declares an optional index key; the harness caches indexes by that key and
records each build's cost separately from query latency. The prepared
retriever returns ranked page slugs and note ids, so the dataset and report
stay the same.
