# Retrieval ablation — R1 (10-page prototype)

> Historical R1 report. Row labels are retrofitted to the E-series; the numbers are unchanged.

- Dataset: `.scratch/rag/prototype/queries.jsonl` — 40 queries, sha256 `8067efbb5c595853b3ab138992efd12890e8f6af65fb75610933948ae6af8658`
- Corpus: `.scratch/rag/prototype` — sha256 `d37ff6417e5534119c40dd951baa386972a82265e3f626b1c50656f041d393cf`
- Scope: main **wiki-only**; secondary **wiki+notes**. The notes queries are excluded from the main table.
- Bootstrap: paired, 10,000 resamples, 95% percentile CI, seed 20261119; no p-values.
- Exploratory: per-bucket CIs and gate curves; no operating point or decision rule is validated.

## Channel rows — main (wiki-only)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E00 lexical (historical) | 34 | 0.357 | — | 0.357 | 0.357 | — | 0.357 |
| E02 BM25-only | 34 | 0.970 | -0.012 [-0.036, +0.000] | 1.000 | 0.961 | -0.017 [-0.037, -0.002] | 0.973 |
| E03 vector-only | 34 | 1.000 | +0.018 [+0.000, +0.054] | 1.000 | 0.993 | +0.016 [-0.002, +0.045] | 1.000 |
| E01 hybrid (reference) | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.978 | +0.000 [+0.000, +0.000] | 0.982 |

## Channel rows — per-bucket Recall@5 — main (wiki-only)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E00 lexical (historical) | 1.000 | 0.000 | 0.000 | — |
| E02 BM25-only | 1.000 | 1.000 | 0.861 | — |
| E03 vector-only | 1.000 | 1.000 | 1.000 | — |
| E01 hybrid (reference) | 1.000 | 1.000 | 0.917 | — |

## Channel rows — secondary (wiki+notes)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E00 lexical (historical) | 40 | 0.294 | — | 0.294 | 0.294 | — | 0.294 |
| E02 BM25-only | 40 | 0.946 | -0.039 [-0.108, +0.000] | 0.971 | 0.939 | -0.043 [-0.108, -0.003] | 0.949 |
| E03 vector-only | 40 | 1.000 | +0.015 [+0.000, +0.044] | 1.000 | 0.994 | +0.013 [-0.001, +0.037] | 1.000 |
| E01 hybrid (reference) | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.982 | +0.000 [+0.000, +0.000] | 0.985 |

## Channel rows — per-bucket Recall@5 — secondary (wiki+notes)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E00 lexical (historical) | 1.000 | 0.000 | 0.000 | 0.000 |
| E02 BM25-only | 1.000 | 1.000 | 0.861 | 0.833 |
| E03 vector-only | 1.000 | 1.000 | 1.000 | 1.000 |
| E01 hybrid (reference) | 1.000 | 1.000 | 0.917 | 1.000 |

## Module ablation rows — main (wiki-only)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E01 reference | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.978 | +0.000 [+0.000, +0.000] | 0.982 |
| E04 whole-page | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.959 | -0.019 [-0.050, +0.000] | 0.958 |
| E05 max aggregation | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.978 | +0.000 [+0.000, +0.000] | 0.982 |
| E06 min-max + equal weight | 34 | 1.000 | +0.018 [+0.000, +0.054] | 1.000 | 0.980 | +0.002 [+0.000, +0.006] | 0.982 |
| E07 prefix off | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.977 | -0.001 [-0.003, +0.000] | 0.982 |
| E08 exact tier off | 34 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.965 | -0.013 [-0.040, +0.000] | 0.964 |

## Module ablation rows — per-bucket Recall@5 — main (wiki-only)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E01 reference | 1.000 | 1.000 | 0.917 | — |
| E04 whole-page | 1.000 | 1.000 | 0.917 | — |
| E05 max aggregation | 1.000 | 1.000 | 0.917 | — |
| E06 min-max + equal weight | 1.000 | 1.000 | 1.000 | — |
| E07 prefix off | 1.000 | 1.000 | 0.917 | — |
| E08 exact tier off | 1.000 | 1.000 | 0.917 | — |

## Module ablation rows — secondary (wiki+notes)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E01 reference | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.982 | +0.000 [+0.000, +0.000] | 0.985 |
| E04 whole-page | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.966 | -0.016 [-0.041, +0.000] | 0.966 |
| E05 max aggregation | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.982 | +0.000 [+0.000, +0.000] | 0.985 |
| E06 min-max + equal weight | 40 | 1.000 | +0.015 [+0.000, +0.044] | 1.000 | 0.983 | +0.002 [+0.000, +0.005] | 0.985 |
| E07 prefix off | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.981 | -0.001 [-0.003, +0.000] | 0.985 |
| E08 exact tier off | 40 | 0.985 | +0.000 [+0.000, +0.000] | 1.000 | 0.971 | -0.011 [-0.033, +0.000] | 0.971 |

## Module ablation rows — per-bucket Recall@5 — secondary (wiki+notes)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E01 reference | 1.000 | 1.000 | 0.917 | 1.000 |
| E04 whole-page | 1.000 | 1.000 | 0.917 | 1.000 |
| E05 max aggregation | 1.000 | 1.000 | 0.917 | 1.000 |
| E06 min-max + equal weight | 1.000 | 1.000 | 1.000 | 1.000 |
| E07 prefix off | 1.000 | 1.000 | 0.917 | 1.000 |
| E08 exact tier off | 1.000 | 1.000 | 0.917 | 1.000 |

## Gate curves (exploratory)

| Variant | Signal | Threshold | Answerable Recall@5 | Gap abstention | Answerable gated |
|---|---|---:|---:|---:|---:|
| E02 BM25-only | term coverage | 0.10 | 0.970 | 0.667 | 0 |
| E02 BM25-only | term coverage | 0.20 | 0.970 | 0.667 | 0 |
| E02 BM25-only | term coverage | 0.30 | 0.934 | 0.833 | 1 |
| E02 BM25-only | term coverage | 0.40 | 0.809 | 1.000 | 5 |
| E02 BM25-only | term coverage | 0.50 | 0.631 | 1.000 | 10 |
| E03 vector-only | top cosine | 0.10 | 1.000 | 0.000 | 0 |
| E03 vector-only | top cosine | 0.20 | 1.000 | 0.500 | 0 |
| E03 vector-only | top cosine | 0.30 | 1.000 | 1.000 | 0 |
| E03 vector-only | top cosine | 0.40 | 1.000 | 1.000 | 0 |
| E03 vector-only | top cosine | 0.50 | 1.000 | 1.000 | 0 |
| E03 vector-only | top1−top2 margin | 0.10 | 0.607 | 1.000 | 11 |
| E03 vector-only | top1−top2 margin | 0.20 | 0.429 | 1.000 | 16 |
| E03 vector-only | top1−top2 margin | 0.30 | 0.321 | 1.000 | 19 |
| E03 vector-only | top1−top2 margin | 0.40 | 0.036 | 1.000 | 27 |
| E03 vector-only | top1−top2 margin | 0.50 | 0.036 | 1.000 | 27 |
| E01 hybrid | top cosine | 0.10 | 0.982 | 0.000 | 0 |
| E01 hybrid | top cosine | 0.20 | 0.982 | 0.500 | 0 |
| E01 hybrid | top cosine | 0.30 | 0.982 | 1.000 | 0 |
| E01 hybrid | top cosine | 0.40 | 0.982 | 1.000 | 0 |
| E01 hybrid | top cosine | 0.50 | 0.982 | 1.000 | 0 |
| E01 hybrid | top1−top2 margin | 0.10 | 0.607 | 1.000 | 11 |
| E01 hybrid | top1−top2 margin | 0.20 | 0.429 | 1.000 | 16 |
| E01 hybrid | top1−top2 margin | 0.30 | 0.321 | 1.000 | 19 |
| E01 hybrid | top1−top2 margin | 0.40 | 0.036 | 1.000 | 27 |
| E01 hybrid | top1−top2 margin | 0.50 | 0.036 | 1.000 | 27 |

Threshold sample; full points in the raw gate report; 6 gap queries; exploratory.

## Index builds

| Chunking | Model | Dim | Seconds | Calls | Tokens | Chunks | Bytes |
|---|---|---:|---:|---:|---:|---:|---:|
| heading | qwen3-embedding:0.6b | 1024 | 21.685 | 1 | 14765 | 48 | 452783 |
| whole-page | qwen3-embedding:0.6b | 1024 | 23.421 | 1 | 14392 | 16 | 190407 |

## Notes

- Per-bucket CIs are in `build/evals/rag/first-round.json` under `deltas[].byType`; they are exploratory.
- The tables print Recall@5 buckets; per-bucket Hit@5, nDCG@10 and MRR, and every delta, are in the combined JSON.
- Gate curves are exploratory; their full points are in `build/evals/rag/ablation/gate.json`.
- E09 tokenizer is skipped; E10 rerank is deferred.
- No p-values; no decision rule is pre-registered.
