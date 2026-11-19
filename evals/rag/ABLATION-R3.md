# Retrieval ablation — R3 300-page control

- Dataset: `.scratch/rag/round2/data/scale300/queries.jsonl` — 65 queries, sha256 `7b05ea089ca0005fda0ed6a81db66674bb33579e15dd28ec055c818791755871`
- Corpus: `.scratch/rag/round2/data/scale300` — sha256 `57da0c21f5848541bb9785b0878ad60bcd1fbd50cf048ff526dd096556fa6517`
- Scope: main **wiki-only**; secondary **wiki+notes**. The notes queries are excluded from the main table.
- Bootstrap: paired, 10,000 resamples, 95% percentile CI, seed 20261119; no p-values.
- Exploratory: per-bucket CIs and gate curves; no operating point or decision rule is validated.
- Run note: 40 labeled pages + 260 distractors; 55 answerable wiki queries; exploratory.

## Channel rows — main (wiki-only)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E00 lexical (historical) | 60 | 0.182 | — | 0.182 | 0.182 | — | 0.182 |
| E02 BM25-only (remove vector) | 60 | 0.964 | -0.018 [-0.055, +0.000] | 0.982 | 0.948 | -0.029 [-0.056, -0.008] | 0.948 |
| E03 vector-only (remove BM25) | 60 | 1.000 | +0.018 [+0.000, +0.045] | 1.000 | 0.987 | +0.010 [-0.012, +0.031] | 0.982 |
| E01 hybrid (reference) | 60 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.977 | +0.000 [+0.000, +0.000] | 0.979 |

## Channel rows — per-bucket Recall@5 — main (wiki-only)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E00 lexical (historical) | 1.000 | 0.000 | 0.000 | — |
| E02 BM25-only (remove vector) | 1.000 | 0.975 | 0.800 | — |
| E03 vector-only (remove BM25) | 1.000 | 1.000 | 1.000 | — |
| E01 hybrid (reference) | 1.000 | 1.000 | 0.800 | — |

## Channel rows — secondary (wiki+notes)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E00 lexical (historical) | 65 | 0.167 | — | 0.167 | 0.167 | — | 0.167 |
| E02 BM25-only (remove vector) | 65 | 0.967 | -0.017 [-0.050, +0.000] | 0.983 | 0.952 | -0.026 [-0.050, -0.007] | 0.953 |
| E03 vector-only (remove BM25) | 65 | 1.000 | +0.017 [+0.000, +0.042] | 1.000 | 0.988 | +0.009 [-0.011, +0.029] | 0.983 |
| E01 hybrid (reference) | 65 | 0.983 | +0.000 [+0.000, +0.000] | 1.000 | 0.979 | +0.000 [+0.000, +0.000] | 0.981 |

## Channel rows — per-bucket Recall@5 — secondary (wiki+notes)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E00 lexical (historical) | 1.000 | 0.000 | 0.000 | 0.000 |
| E02 BM25-only (remove vector) | 1.000 | 0.975 | 0.800 | 1.000 |
| E03 vector-only (remove BM25) | 1.000 | 1.000 | 1.000 | 1.000 |
| E01 hybrid (reference) | 1.000 | 1.000 | 0.800 | 1.000 |

## Module ablation rows — main (wiki-only)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E01 reference | 60 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.977 | +0.000 [+0.000, +0.000] | 0.979 |
| E04 whole-page | 60 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.968 | -0.009 [-0.029, +0.005] | 0.964 |
| E05 max aggregation | 60 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.979 | +0.002 [-0.000, +0.007] | 0.982 |
| E06 min-max + equal weight | 60 | 0.991 | +0.009 [+0.000, +0.027] | 1.000 | 0.982 | +0.005 [-0.015, +0.025] | 0.982 |
| E07 prefix off | 60 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.978 | +0.001 [-0.004, +0.007] | 0.982 |
| E08 exact tier off | 60 | 0.982 | +0.000 [+0.000, +0.000] | 1.000 | 0.977 | +0.000 [+0.000, +0.000] | 0.979 |

## Module ablation rows — per-bucket Recall@5 — main (wiki-only)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E01 reference | 1.000 | 1.000 | 0.800 | — |
| E04 whole-page | 1.000 | 1.000 | 0.800 | — |
| E05 max aggregation | 1.000 | 1.000 | 0.800 | — |
| E06 min-max + equal weight | 1.000 | 1.000 | 0.900 | — |
| E07 prefix off | 1.000 | 1.000 | 0.800 | — |
| E08 exact tier off | 1.000 | 1.000 | 0.800 | — |

## Module ablation rows — secondary (wiki+notes)

| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] | Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| E01 reference | 65 | 0.983 | +0.000 [+0.000, +0.000] | 1.000 | 0.979 | +0.000 [+0.000, +0.000] | 0.981 |
| E04 whole-page | 65 | 0.983 | +0.000 [+0.000, +0.000] | 1.000 | 0.970 | -0.009 [-0.027, +0.005] | 0.967 |
| E05 max aggregation | 65 | 0.983 | +0.000 [+0.000, +0.000] | 1.000 | 0.981 | +0.002 [-0.000, +0.007] | 0.983 |
| E06 min-max + equal weight | 65 | 0.992 | +0.008 [+0.000, +0.025] | 1.000 | 0.983 | +0.004 [-0.014, +0.023] | 0.983 |
| E07 prefix off | 65 | 0.983 | +0.000 [+0.000, +0.000] | 1.000 | 0.980 | +0.001 [-0.004, +0.007] | 0.983 |
| E08 exact tier off | 65 | 0.983 | +0.000 [+0.000, +0.000] | 1.000 | 0.979 | +0.000 [+0.000, +0.000] | 0.981 |

## Module ablation rows — per-bucket Recall@5 — secondary (wiki+notes)

| Row | exact | paraphrase | cross_page | notes |
|---|---:|---:|---:|---:|
| E01 reference | 1.000 | 1.000 | 0.800 | 1.000 |
| E04 whole-page | 1.000 | 1.000 | 0.800 | 1.000 |
| E05 max aggregation | 1.000 | 1.000 | 0.800 | 1.000 |
| E06 min-max + equal weight | 1.000 | 1.000 | 0.900 | 1.000 |
| E07 prefix off | 1.000 | 1.000 | 0.800 | 1.000 |
| E08 exact tier off | 1.000 | 1.000 | 0.800 | 1.000 |

## Gate curves (exploratory)

| Variant | Signal | Threshold | Answerable Recall@5 | Gap abstention | Answerable gated |
|---|---|---:|---:|---:|---:|
| E02 BM25-only | term coverage | 0.10 | 0.964 | 0.200 | 0 |
| E02 BM25-only | term coverage | 0.20 | 0.946 | 0.200 | 1 |
| E02 BM25-only | term coverage | 0.30 | 0.927 | 0.600 | 2 |
| E02 BM25-only | term coverage | 0.40 | 0.754 | 1.000 | 12 |
| E02 BM25-only | term coverage | 0.50 | 0.609 | 1.000 | 21 |
| E03 Vector-only | top cosine | 0.10 | 1.000 | 0.000 | 0 |
| E03 Vector-only | top cosine | 0.20 | 1.000 | 0.000 | 0 |
| E03 Vector-only | top cosine | 0.30 | 1.000 | 0.400 | 0 |
| E03 Vector-only | top cosine | 0.40 | 1.000 | 1.000 | 0 |
| E03 Vector-only | top cosine | 0.50 | 1.000 | 1.000 | 0 |
| E03 Vector-only | top1−top2 margin | 0.10 | 0.891 | 1.000 | 6 |
| E03 Vector-only | top1−top2 margin | 0.20 | 0.618 | 1.000 | 21 |
| E03 Vector-only | top1−top2 margin | 0.30 | 0.309 | 1.000 | 38 |
| E03 Vector-only | top1−top2 margin | 0.40 | 0.054 | 1.000 | 52 |
| E03 Vector-only | top1−top2 margin | 0.50 | 0.000 | 1.000 | 55 |
| E01 Hybrid | top cosine | 0.10 | 0.982 | 0.000 | 0 |
| E01 Hybrid | top cosine | 0.20 | 0.982 | 0.000 | 0 |
| E01 Hybrid | top cosine | 0.30 | 0.982 | 0.400 | 0 |
| E01 Hybrid | top cosine | 0.40 | 0.982 | 1.000 | 0 |
| E01 Hybrid | top cosine | 0.50 | 0.982 | 1.000 | 0 |
| E01 Hybrid | top1−top2 margin | 0.10 | 0.873 | 1.000 | 6 |
| E01 Hybrid | top1−top2 margin | 0.20 | 0.609 | 1.000 | 21 |
| E01 Hybrid | top1−top2 margin | 0.30 | 0.309 | 1.000 | 38 |
| E01 Hybrid | top1−top2 margin | 0.40 | 0.054 | 1.000 | 52 |
| E01 Hybrid | top1−top2 margin | 0.50 | 0.000 | 1.000 | 55 |

Threshold sample; full points in the raw gate report; 5 gap queries; exploratory.

## Index builds

| Chunking | Model | Dim | Seconds | Calls | Tokens | Chunks | Bytes |
|---|---|---:|---:|---:|---:|---:|---:|
| heading | qwen3-embedding:0.6b | 1024 | 984.425 | 23 | 687426 | 1449 | 14251924 |
| whole-page | qwen3-embedding:0.6b | 1024 | 1125.045 | 5 | 642805 | 305 | 4867809 |

## Notes

- Per-bucket CIs are in `build/evals/rag/r3.json` under `deltas[].byType`; they are exploratory.
- The tables print Recall@5 buckets; per-bucket Hit@5, nDCG@10 and MRR, and every delta, are in the combined JSON.
- Gate curves are exploratory; their full points are in `build/evals/rag/ablation-r3/gate.json`.
- E09 tokenizer is skipped; E10 rerank is deferred.
- No p-values; no decision rule is pre-registered.
