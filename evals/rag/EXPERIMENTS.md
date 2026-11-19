# RAG retrieval experiment register

Every ranking experiment is a **module configuration**. The reference pipeline
is one combination of module choices; an ablation changes one or more choices
and compares against the reference. “Swap” is only a mechanism (choosing a
different implementation of the same module), not a separate category. The old
`S1`–`S6` names are retired; use the mapping at the bottom.

## Terms

- **Single-module ablation (OFAT)**: exactly one module in the reference
  configuration is changed. The current E02–E08 rows are OFAT. This is the
  scope of the next run; multi-module combinations are out of scope.
- **Multi-module combination**: two or more modules changed at once (for
  example whole-page + max, or no vector + no exact). Not tested; no
  interaction terms are estimated.
- **R3 300-page control**: 300 wiki files, but only **40 are labeled** and
  **260 are unlabeled distractors**. Results on R3 are exploratory at the
  40 labeled pages / 55 answerable wiki queries (60 wiki queries, 5 gap).
  “300 pages” does not mean 300 labeled pages.

## Modules

| Module | Reference choice | Alternative / off | Experiment |
|---|---|---|---|
| **M1** Chunking | heading chunks (1000/1500/200/200) | whole-page one chunk | E04 |
| **M2** Lexical channel | BM25F (CJK bigram) | off | E03 |
| **M3** Vector channel | Qwen3-Embedding 0.6B, 1024-d | off | E02 |
| **M4** Page aggregation | `best + min(0.3×mean(rest), 1−best)` | `max` | E05 |
| **M5** Fusion | RRF k=60 | per-query min-max + 0.5/0.5 | E06 |
| **M6** Exact tier | on | off | E08 |
| **M7** Query prefix | Qwen3 instruction prefix on | off | E07 |
| **M8** Rerank | off | Cohere-compatible on | E10 |
| **M9** Tokenizer | CJK bigram | alternative | E09 |

Reference configuration: `M1 heading · M2 BM25 · M3 Qwen3 · M4 tail · M5 RRF · M6 exact on · M7 prefix on · M8 rerank off · M9 bigram`.

## Runs

| Run | Corpus | Labeled pages | Queries | Scope | What ran |
|---|---|---:|---|---|---|
| **R1** | 10-page hand-labeled prototype + 6 notes | 10 | 40 (34 wiki + 6 notes) | wiki-only n=34; wiki+notes n=40 | Full matrix: E00–E08 + G |
| **R2** | 40-page MIRACL pilot + 5 notes | 40 | 65 (60 wiki + 5 notes) | wiki-only n=60; wiki+notes n=65 | Channel only: E00–E03 |
| **R3** | 300-page control: 40 pilot pages + 260 unlabeled distractors + 5 notes | 40 | same 65 | wiki-only n=60; wiki+notes n=65 | Full matrix: E00–E08 + G |

R2 and R3 are **data/difficulty controls**, not full matrices. Do not cite an
R1 module result as if it were measured on R2 or R3.

Legend: ✓ run · — not run · ⏭ skipped · ⏸ deferred · ⚠️ exploratory.

## Module configuration matrix

Every row below is one module combination. The changed cell is bold.

| ID | M1 分块 | M2 BM25 | M3 向量 | M4 聚合 | M5 融合 | M6 exact | M7 prefix | M8 rerank | M9 分词 |
|---|---|---|---|---|---|---|---|---|---|
| **E00** | — | 旧 `rank_text` | — | — | — | 旧规则 | — | — | — |
| **E01** | heading | on | on | tail | RRF | on | on | off | bigram |
| **E02** | heading | on | **off** | — | — | on | — | off | bigram |
| **E03** | heading | **off** | on | tail | — | independent | on | off | bigram |
| **E04** | **whole-page** | on | on | tail* | RRF | on | on | off | bigram |
| **E05** | heading | on | on | **max** | RRF | on | on | off | bigram |
| **E06** | heading | on | on | tail | **min-max/equal** | on | on | off | bigram |
| **E07** | heading | on | on | tail | RRF | on | **off** | off | bigram |
| **E08** | heading | on | on | tail | RRF | **off** | on | off | bigram |
| **E09** | heading | on | on | tail | RRF | on | on | off | **alt** |
| **E10** | heading | on | on | tail | RRF | on | on | **on** | bigram |

`*` E04 still uses the tail formula, but one chunk per page makes the tail term
zero; effectively max. The configuration is recorded as tail because the
aggregation module was not changed.

## Ranking experiments

| ID | Name | Module changed | Sub-kind | R1 | R2 | R3 |
|---|---|---|---|---|---|---|
| **E00** | Historical lexical baseline | — (different pipeline) | baseline | ✓ | ✓ | ✓ |
| **E01** | Reference hybrid | — | reference | ✓ | ✓ | ✓ |
| **E02** | Remove vector (BM25-only) | M3 | removal | ✓ | ✓ | ✓ |
| **E03** | Remove BM25 (vector-only) | M2 | removal | ✓ | ✓ | ✓ |
| **E04** | Whole-page chunking | M1 | variant | ✓ | — | ✓ |
| **E05** | Max page aggregation | M4 | variant | ✓ | — | ✓ |
| **E06** | Min-max + equal-weight fusion | M5 | variant | ✓ | — | ✓ |
| **E07** | Query prefix off | M7 | removal | ✓ | — | ✓ |
| **E08** | Exact tier off | M6 | removal | ✓ | — | ✓ |
| **E09** | Tokenizer swap | M9 | variant | ⏭ | — | — |
| **E10** | Add rerank | M8 | addition | ⏸ | — | — |

**All of E02–E10 are module ablations.** The sub-kind only says how the module
changed: removed, replaced by a variant, or added.

## Gate experiments

Gate is an added module after ranking, not a ranking-row module. Gate curves
exist for R1 (6 gap) and R3 (5 gap); both are **exploratory**; no operating
point is validated.

| ID | Name | Signal | Threshold sample | R1 answerable | R1 gap | R3 answerable | R3 gap |
|---|---|---|---:|---:|---:|---:|---:|
| **G01** | BM25 coverage gate | Query-token coverage of top BM25 page | 0.40 | 0.809 | 6/6 | 0.754 | 5/5 |
| **G02** | Vector cosine gate | Raw top-chunk cosine | 0.40–0.50 | 1.000 | 6/6 | 1.000 | 5/5 |
| **G03** | Vector margin gate | top1−top2 raw cosine | 0.10 | 0.607 | 6/6 | 0.891 | 5/5 |
| **G04** | Hybrid cosine gate | Same vector signal, hybrid ranking | 0.40 | 0.982 | 6/6 | 0.982 | 5/5 |
| **G05** | Hybrid margin gate | Same vector signal, hybrid ranking | 0.10 | 0.607 | 6/6 | 0.873 | 5/5 |

## Parameter sensitivities (not run)

| ID | Name | Status |
|---|---|---|
| **P01** | Chunk target / max / overlap / min | — |
| **P02** | RRF `k` | — |
| **P03** | Candidate depth (eval uses 20; production 50) | — |
| **P04** | Fusion weights | — |
| **P05** | BM25 `k1` / `b` / title-body weights | — |
| **P06** | Output top-k | — |

## Results — channel rows by run (E00–E03)

Values are **Recall@5**; references are per run, not cross-run comparable.

| Run | Corpus | Queries | E00 lexical | E02 BM25-only | E03 vector-only | E01 hybrid |
|---|---|---:|---:|---:|---:|---:|
| R1 | 10 pages | 34 | 0.357 | 0.970 | 1.000 | 0.982 |
| R2 | 40 pages | 60 | 0.182 | 0.991 | 1.000 | 1.000 |
| R3 | 300 pages | 60 | 0.182 | 0.964 | 1.000 | 0.982 |

R3 nDCG@10: E02 0.948 · E03 0.987 · E01 0.977.  
R2 nDCG@10: E02 0.979 · E03 0.993 · E01 0.990.

## Results — module ablations on R1 (10 pages, E04–E08)

| ID | Recall@5 | Δ Recall@5 [95% CI] | nDCG@10 | Δ nDCG@10 [95% CI] | MRR | Verdict |
|---|---:|---:|---:|---:|---:|---|
| **E04** whole-page | 0.982 | 0.000 [+0.000, +0.000] | 0.959 | -0.019 [-0.050, +0.000] | 0.958 | not established |
| **E05** max aggregation | 0.982 | 0.000 | 0.978 | 0.000 | 0.982 | no change |
| **E06** min-max + equal | 1.000 | +0.018 [+0.000, +0.054] | 0.980 | +0.002 [+0.000, +0.006] | 0.982 | not established (one cross-page page) |
| **E07** prefix off | 0.982 | 0.000 | 0.977 | -0.001 [-0.003, +0.000] | 0.982 | no change |
| **E08** exact off | 0.982 | 0.000 | 0.965 | -0.013 [-0.040, +0.000] | 0.964 | not established |

E01 reference on R1: Recall@5 0.982 · nDCG@10 0.978 · MRR 0.982.

## Results — module ablations on R3 (40 labeled + 260 distractors)

| ID | Recall@5 | Δ Recall@5 [95% CI] | nDCG@10 | Δ nDCG@10 [95% CI] | MRR | Verdict |
|---|---:|---:|---:|---:|---:|---|
| **E04** whole-page | 0.982 | 0.000 [+0.000, +0.000] | 0.968 | -0.009 [-0.029, +0.005] | 0.964 | not established |
| **E05** max aggregation | 0.982 | 0.000 | 0.979 | +0.002 [-0.000, +0.007] | 0.982 | no reliable change |
| **E06** min-max + equal | 0.991 | +0.009 [+0.000, +0.027] | 0.982 | +0.005 [-0.015, +0.025] | 0.982 | not established (one cross-page page) |
| **E07** prefix off | 0.982 | 0.000 | 0.978 | +0.001 [-0.004, +0.007] | 0.982 | no change |
| **E08** exact off | 0.982 | 0.000 | 0.977 | 0.000 | 0.979 | no change |

E01 reference on R3: Recall@5 0.982 · nDCG@10 0.977 · MRR 0.979.  
R3 is exploratory at 40 labeled pages / 55 answerable wiki queries.

## Combination coverage

The current design is **one-factor-at-a-time (OFAT)**: E02–E08 each change
exactly one module against E01. It shows each module's individual effect, not
their interactions.

Tested combinations:

- E01 = the full reference combination.
- E02–E08 = reference with exactly one module changed.

Not tested:

- Multi-module combinations, e.g. E04 × E05 (whole-page + max), E02 × E08
  (no vector + no exact), E04 × E06 (whole-page + min-max), E03 × E07
  (no BM25 + no prefix).
- A full factorial design over M1–M7; no interaction terms are estimated.
- Any combination on R2 or R3.

## Old name → new ID

| Old | New | Note |
|---|---|---|
| `lexical` | **E00** | Historical baseline |
| `hybrid` | **E01** | Reference |
| `BM25-only` | **E02** | Remove vector (M3) |
| `vector-only` | **E03** | Remove BM25 (M2) |
| `S1` whole-page | **E04** | Chunking variant (M1) |
| `S2` max | **E05** | Aggregation variant (M4) |
| `S3` min-max | **E06** | Fusion variant (M5) |
| `S4` prefix off | **E07** | Prefix removal (M7) |
| `S5` tokenizer | **E09** | Tokenizer variant (M9); skipped |
| `S6` exact off | **E08** | Exact-tier removal (M6) |
| `hybrid+rerank` | **E10** | Rerank addition (M8); deferred |
| BM25 coverage gate | **G01** | Gate |
| vector cosine gate | **G02** | Gate |
| vector margin gate | **G03** | Gate |
| hybrid cosine gate | **G04** | Gate |
| hybrid margin gate | **G05** | Gate |

## Not measured

- E09 (tokenizer) and E10 (rerank).
- Any P01–P06 sensitivity.
- E04–E08 on R2 only (R3 has them now).
- G01–G05 on R2 only (R3 has them now).
- Bootstrap/CIs on R2 only (R3 has them now).
- Multi-module combinations and interactions (the OFAT gap above).
- Hard-exact, section-specific, true cross-page, and scaled gap queries.
- A final 300–500-page corpus with labels for all pages; R3 has 260 unlabeled
  distractors and only 40 labeled pages / 55 answerable wiki queries.
- Production `RagSearchBackend`, production latency/concurrency, and rerank.
- Primary metric, decision rule, power analysis, and label exhaustiveness.

## Rules

1. Refer to experiments by `E`/`G`/`P` ID, not by `S` number.
2. Every E02–E10 is a module ablation; use “removal / variant / addition” for
   the mechanism, never “not an ablation”.
3. A result is comparable only within its run; E00–E03 appear in R1/R2/R3, but
   the corpora and query sets differ.
4. E04–E08 ran on R1 and R3; R2 remains channel-only.
5. Gate is a module added after ranking; report it separately.
6. R3 is a 300-page control with **40 labeled pages**; its matrix is
   exploratory at those 40 pages / 55 answerable queries, not a 300-page
   generalization.
