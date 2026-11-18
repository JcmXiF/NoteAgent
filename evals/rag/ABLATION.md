# Retrieval ablation — first round

- Dataset: `.scratch/rag/prototype/queries.jsonl` — 40 queries, sha256 `8067efbb5c595853b3ab138992efd12890e8f6af65fb75610933948ae6af8658`
- Corpus: `.scratch/rag/prototype` — sha256 `d37ff6417e5534119c40dd951baa386972a82265e3f626b1c50656f041d393cf`
- Scope: main **wiki-only**; secondary **wiki+notes**. The notes queries are excluded from the main table.
- Bootstrap: paired, 10,000 resamples, 95% percentile CI, seed 20261119; no p-values.
- Exploratory: per-bucket CIs and gate curves; no operating point or decision rule is validated.

## Channel rows — main (wiki-only)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical (historical) | 34 | 0.357 | — | 0.357 | 0.357 | — | 0.357 |
| BM25-only | 34 | 0.970 | -0.012 [-0.036, +0.000] | 1.000 | 0.961 | -0.017 [-0.037, -0.002] | 0.973 |
| Vector-only | 34 | 1.000 | +0.018 [+0.000, +0.054] | 1.000 | 0.993 | +0.016 [-0.002, +0.045] | 1.000 |
| Hybrid (reference) | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.978 | +0.000 [+0.000, +0.000] | 0.982 |

## Channel rows — per-bucket Recall@5 — main (wiki-only)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| lexical (historical) | 1.000 | 0.000 | 0.000 | — |
| BM25-only | 1.000 | 1.000 | 0.861 | — |
| Vector-only | 1.000 | 1.000 | 1.000 | — |
| Hybrid (reference) | 1.000 | 1.000 | 0.917 | — |

## Channel rows — secondary (wiki+notes)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical (historical) | 40 | 0.294 | — | 0.294 | 0.294 | — | 0.294 |
| BM25-only | 40 | 0.946 | -0.039 [-0.108, +0.000] | 0.971 | 0.939 | -0.043 [-0.108, -0.003] | 0.949 |
| Vector-only | 40 | 1.000 | +0.015 [+0.000, +0.044] | 1.000 | 0.994 | +0.013 [-0.001, +0.037] | 1.000 |
| Hybrid (reference) | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.982 | +0.000 [+0.000, +0.000] | 0.985 |

## Channel rows — per-bucket Recall@5 — secondary (wiki+notes)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| lexical (historical) | 1.000 | 0.000 | 0.000 | 0.000 |
| BM25-only | 1.000 | 1.000 | 0.861 | 0.833 |
| Vector-only | 1.000 | 1.000 | 1.000 | 1.000 |
| Hybrid (reference) | 1.000 | 1.000 | 0.917 | 1.000 |

## Swap rows — main (wiki-only)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| reference | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.978 | +0.000 [+0.000, +0.000] | 0.982 |
| S2 max aggregation | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.978 | +0.000 [+0.000, +0.000] | 0.982 |
| S3 min-max + equal weight | 34 | 1.000 | +0.018 [+0.000, +0.054] | 1.000 | 0.980 | +0.002 [+0.000, +0.006] | 0.982 |
| S4 prefix off | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.977 | -0.001 [-0.003, +0.000] | 0.982 |
| S6 exact tier off | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.965 | -0.013 [-0.040, +0.000] | 0.964 |
| S1 whole-page | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.959 | -0.019 [-0.050, +0.000] | 0.958 |

## Swap rows — per-bucket Recall@5 — main (wiki-only)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| reference | 1.000 | 1.000 | 0.917 | — |
| S2 max aggregation | 1.000 | 1.000 | 0.917 | — |
| S3 min-max + equal weight | 1.000 | 1.000 | 1.000 | — |
| S4 prefix off | 1.000 | 1.000 | 0.917 | — |
| S6 exact tier off | 1.000 | 1.000 | 0.917 | — |
| S1 whole-page | 1.000 | 1.000 | 0.917 | — |

## Swap rows — secondary (wiki+notes)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| reference | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.982 | +0.000 [+0.000, +0.000] | 0.985 |
| S2 max aggregation | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.982 | +0.000 [+0.000, +0.000] | 0.985 |
| S3 min-max + equal weight | 40 | 1.000 | +0.015 [+0.000, +0.044] | 1.000 | 0.983 | +0.002 [+0.000, +0.005] | 0.985 |
| S4 prefix off | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.981 | -0.001 [-0.003, +0.000] | 0.985 |
| S6 exact tier off | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.971 | -0.011 [-0.033, +0.000] | 0.971 |
| S1 whole-page | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.966 | -0.016 [-0.041, +0.000] | 0.966 |

## Swap rows — per-bucket Recall@5 — secondary (wiki+notes)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| reference | 1.000 | 1.000 | 0.917 | 1.000 |
| S2 max aggregation | 1.000 | 1.000 | 0.917 | 1.000 |
| S3 min-max + equal weight | 1.000 | 1.000 | 1.000 | 1.000 |
| S4 prefix off | 1.000 | 1.000 | 0.917 | 1.000 |
| S6 exact tier off | 1.000 | 1.000 | 0.917 | 1.000 |
| S1 whole-page | 1.000 | 1.000 | 0.917 | 1.000 |

## Gate curves (exploratory)

| Variant | Signal | Threshold | Answerable Recall@5 | Gap abstention | Answerable gated |
|---|---|---:|---:|---:|---:|
| BM25-only | term coverage | 0.10 | 0.970 | 0.667 | 0 |
| BM25-only | term coverage | 0.20 | 0.970 | 0.667 | 0 |
| BM25-only | term coverage | 0.30 | 0.934 | 0.833 | 1 |
| BM25-only | term coverage | 0.40 | 0.809 | 1.000 | 5 |
| BM25-only | term coverage | 0.50 | 0.631 | 1.000 | 10 |
| Vector-only | top cosine | 0.10 | 1.000 | 0.000 | 0 |
| Vector-only | top cosine | 0.20 | 1.000 | 0.500 | 0 |
| Vector-only | top cosine | 0.30 | 1.000 | 1.000 | 0 |
| Vector-only | top cosine | 0.40 | 1.000 | 1.000 | 0 |
| Vector-only | top cosine | 0.50 | 1.000 | 1.000 | 0 |
| Vector-only | top1−top2 margin | 0.10 | 0.607 | 1.000 | 11 |
| Vector-only | top1−top2 margin | 0.20 | 0.429 | 1.000 | 16 |
| Vector-only | top1−top2 margin | 0.30 | 0.321 | 1.000 | 19 |
| Vector-only | top1−top2 margin | 0.40 | 0.036 | 1.000 | 27 |
| Vector-only | top1−top2 margin | 0.50 | 0.036 | 1.000 | 27 |
| Hybrid | top cosine | 0.10 | 0.982 | 0.000 | 0 |
| Hybrid | top cosine | 0.20 | 0.982 | 0.500 | 0 |
| Hybrid | top cosine | 0.30 | 0.982 | 1.000 | 0 |
| Hybrid | top cosine | 0.40 | 0.982 | 1.000 | 0 |
| Hybrid | top cosine | 0.50 | 0.982 | 1.000 | 0 |
| Hybrid | top1−top2 margin | 0.10 | 0.607 | 1.000 | 11 |
| Hybrid | top1−top2 margin | 0.20 | 0.429 | 1.000 | 16 |
| Hybrid | top1−top2 margin | 0.30 | 0.321 | 1.000 | 19 |
| Hybrid | top1−top2 margin | 0.40 | 0.036 | 1.000 | 27 |
| Hybrid | top1−top2 margin | 0.50 | 0.036 | 1.000 | 27 |

Threshold sample; full points in the raw gate report; 6 gap queries; exploratory.

## Index builds

| Chunking | Model | Dim | Seconds | Calls | Tokens | Chunks | Bytes |
|---|---|---:|---:|---:|---:|---:|---:|
| heading | qwen3-embedding:0.6b | 1024 | 21.685 | 1 | 14765 | 48 | 452783 |
| whole-page | qwen3-embedding:0.6b | 1024 | 23.421 | 1 | 14392 | 16 | 190407 |

## Notes

- Per-bucket CIs are in `build/evals/rag/first-round.json` under `deltas[].byType`; they are exploratory.
- Gate curves are exploratory; their full points are in `build/evals/rag/ablation/gate.json`.
- S5 tokenizer is skipped; `hybrid+rerank` is deferred.
- No p-values; no decision rule is pre-registered.
