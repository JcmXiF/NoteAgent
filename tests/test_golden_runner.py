"""The golden runner, manifest, report and paired reading, without a network."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_fakes import FakeModelClient

from evals.golden.dataset import (
    CorpusPage,
    ExpectedTrace,
    GoldenCorpus,
    GoldenQuery,
    GoldenSet,
)
from evals.golden.manifest import (
    QueryRecord,
    RunRecord,
    ScoreRecord,
    ab_manifest_payload,
    append_history,
    history_row,
    manifest_payload,
    public_base_url,
    read_history,
)
from evals.golden.report import write_ab_report, write_variant_report
from evals.golden.runner import (
    answer_text,
    pair_variants,
    record_for,
    run_variant,
    sections_sha256,
    seed_corpus,
    system_sections,
)
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.model import (
    AssistantMessage,
    ModelDoneEvent,
    ModelStartEvent,
    ModelTextDelta,
    ModelToolCallEnd,
    TextBlock,
    ToolCall,
)
from noteagent.llm.client import LlmTurn, TokenLogprob
from noteagent.llm.config import LlmConfig
from noteagent.project.init import init_project

SCORES = (5, 4, 3, 2, 1)
CONFIG = LlmConfig(
    "https://user:pass@model.test/v1",
    "SECRET-KEY",
    "deepseek-test",
    temperature=0.1,
)


class FakeJudge:
    """Three scores, one per dimension, with logprobs for each."""

    def __init__(self, scores: tuple[int, int, int]) -> None:
        self.scores = scores
        self.calls: list[dict] = []

    def complete(self, messages, **kwargs) -> LlmTurn:
        index = min(len(self.calls), len(self.scores) - 1)
        score = self.scores[index]
        self.calls.append({"messages": messages, "kwargs": kwargs})
        top = tuple(
            (
                f" {value}",
                0.0 if value == score else -60.0,
            )
            for value in SCORES
        )
        return LlmTurn(
            content=f"Reason {score}.\nScore: {score}",
            logprobs=(TokenLogprob(f" {score}", 0.0, top),),
        )


def tiny_golden(root: Path, *, question: str = "X 是什么？") -> GoldenSet:
    page_path = root / "corpus" / "wiki" / "concepts" / "x.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(
        '---\ntitle: "X"\ntype: concept\n---\n\nX 的定义。\n',
        encoding="utf-8",
    )
    corpus = GoldenCorpus(
        root=root / "corpus",
        pages=(
            CorpusPage(
                "concepts/x", "X", "X 的定义。", page_path
            ),
        ),
        purpose="# Purpose\n\n信息论。",
        schema="# Schema\n\nconcept",
    )
    case = GoldenQuery(
        id="case-1",
        type="fact",
        difficulty="easy",
        question=question,
        corpus_pages=(),
        reference_answer="X 的定义。",
        key_points=("X 的定义", "X 的含义"),
        expected_trace=ExpectedTrace(
            tools_any=(),
            wait="absent",
            max_tool_rounds=0,
            no_error=True,
        ),
        forbidden=("编造",),
        raw_sources=(),
        notes="",
    )
    return GoldenSet(
        root=root,
        corpus=corpus,
        queries=(case,),
        manifest={},
        counts={},
        corpus_sha256="corpus-hash",
        queries_sha256="queries-hash",
    )


class SystemSectionTests(unittest.TestCase):
    def test_base_and_strict_differ_only_in_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden = tiny_golden(Path(tmp))
            base = system_sections(golden, "base")
            strict = system_sections(golden, "strict")

        self.assertEqual(
            set(base), {"identity", "tools", "project", "grounding"}
        )
        self.assertEqual(base["identity"], strict["identity"])
        self.assertEqual(base["tools"], strict["tools"])
        self.assertEqual(base["project"], strict["project"])
        self.assertNotEqual(base["grounding"], strict["grounding"])
        self.assertIn(
            "do not propose a job just because", base["tools"]
        )
        self.assertIn(
            "Only when the reader explicitly asks", base["tools"]
        )
        self.assertIn("If the wiki lacks a page", base["tools"])
        self.assertIn("信息论", base["project"])
        self.assertNotEqual(
            sections_sha256(base), sections_sha256(strict)
        )

    def test_an_unknown_variant_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden = tiny_golden(Path(tmp))
            with self.assertRaises(ValueError):
                system_sections(golden, "loose")


class SeedCorpusTests(unittest.TestCase):
    def test_the_pages_keep_their_frontmatter_and_raw_stays_empty(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = tiny_golden(root)
            layout = init_project(root / "project")

            seed_corpus(layout, golden)

            page = layout.wiki / "concepts" / "x.md"
            self.assertEqual(
                page.read_text(encoding="utf-8"),
                golden.corpus.pages[0].path.read_text(encoding="utf-8"),
            )
            self.assertIn("信息论", layout.brief.read_text(encoding="utf-8"))
            self.assertIn(
                "concept", layout.legacy_schema.read_text(encoding="utf-8")
            )
            self.assertEqual(list(layout.raw.iterdir()), [])


class AnswerTextTests(unittest.TestCase):
    def test_the_final_answer_is_used(self) -> None:
        text = answer_text(
            [
                {
                    "role": "assistant",
                    "content": "我先查一下。",
                    "toolCalls": [{"id": "c1", "name": "wiki_read"}],
                },
                {"role": "assistant", "content": "X 的定义。"},
            ]
        )

        self.assertEqual(text, "X 的定义。")

    def test_a_proposal_is_part_of_the_answer(self) -> None:
        text = answer_text(
            [
                {
                    "type": "wait_proposed",
                    "job": "plan",
                    "interface": "plan_board",
                    "intent": "整理学习路径",
                    "reason": "需要人进入计划板",
                }
            ]
        )

        self.assertIn("job=plan, interface=plan_board", text)
        self.assertIn("Intent: 整理学习路径", text)
        self.assertIn("Reason: 需要人进入计划板", text)


class RunVariantTests(unittest.TestCase):
    def run_one(self, root: Path):
        golden = tiny_golden(root)
        artifacts = root / "run"
        run = run_variant(
            golden,
            CONFIG,
            artifacts,
            variant="base",
            model=FakeModelClient("X 的定义。"),
            judge_client=FakeJudge((4, 3, 5)),
        )
        return golden, run

    def test_one_query_runs_through_the_agent_and_keeps_its_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _golden, run = self.run_one(Path(tmp))
            query = run.queries[0]
            case_dir = run.run_dir / "queries" / "case-1"

            self.assertTrue(query.passed)
            self.assertEqual(query.answer, "X 的定义。")
            self.assertEqual(
                [item.score for item in query.verdict.scores], [4, 3, 5]
            )
            self.assertEqual(
                run.sections["grounding"],
                system_sections(_golden, "base")["grounding"],
            )
            for name in (
                "case.json",
                "judge.json",
                "session.jsonl",
                "events.jsonl",
            ):
                self.assertTrue(
                    (case_dir / name).is_file(), name
                )
            self.assertTrue(
                (run.run_dir / "project" / "wiki" / "concepts" / "x.md")
                .is_file()
            )
            session = (case_dir / "session.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn('"system_section"', session)
            self.assertIn('"grounding"', session)
            judge = json.loads(
                (case_dir / "judge.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(judge["scores"]), 3)
            self.assertTrue(judge["ok"])

    def test_the_manifest_and_history_never_hold_the_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden, run = self.run_one(root)
            record = record_for(run, golden, CONFIG)
            payload = json.dumps(
                manifest_payload(record), ensure_ascii=False
            )

            self.assertNotIn("SECRET-KEY", payload)
            self.assertNotIn("user:pass", payload)
            self.assertEqual(record.agent_temperature, 0.0)
            self.assertEqual(record.corpus_sha256, "corpus-hash")
            self.assertEqual(record.queries_sha256, "queries-hash")

            history = root / "history.csv"
            append_history(history, record)
            append_history(history, record)
            rows = read_history(history)

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["variant"], "base")
            self.assertEqual(rows[0]["query_count"], "1")
            self.assertEqual(rows[0]["trace_failed"], "0")
            self.assertEqual(rows[0]["grounding_mean"], "4.0")

    def test_the_report_escapes_answers_and_uses_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = tiny_golden(
                root, question="<script>alert(1)</script> 是什么？"
            )
            run = run_variant(
                golden,
                CONFIG,
                root / "run",
                variant="base",
                model=FakeModelClient("<script>alert(1)</script>"),
                judge_client=FakeJudge((4, 4, 4)),
            )
            record = record_for(run, golden, CONFIG)
            json_path = root / "run" / "report.json"
            html_path = root / "run" / "report.html"

            payload = write_variant_report(
                run, record, [], json_path=json_path, html_path=html_path
            )
            page = html_path.read_text(encoding="utf-8")

            self.assertEqual(payload["kind"], "variant")
            self.assertNotIn("<script>alert(1)</script>", page)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
            self.assertNotIn('href="http', page)
            self.assertIn("G-Eval", page)
            self.assertIn("Trend", page)
            self.assertEqual(
                json.loads(json_path.read_text(encoding="utf-8"))[
                    "queries"
                ][0]["id"],
                "case-1",
            )


class ToolRoundTests(unittest.TestCase):
    def test_a_wiki_read_round_reaches_the_tool_and_the_judge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = (
                root / "corpus" / "wiki" / "concepts" / "信息熵.md"
            )
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_text(
                '---\ntitle: "信息熵"\ntype: concept\n---\n\n'
                "熵衡量不确定性。\n",
                encoding="utf-8",
            )
            corpus = GoldenCorpus(
                root=root / "corpus",
                pages=(
                    CorpusPage(
                        "concepts/信息熵",
                        "信息熵",
                        "熵衡量不确定性。",
                        page_path,
                    ),
                ),
                purpose="# Purpose\n\n信息论。",
                schema="# Schema\n\nconcept",
            )
            case = GoldenQuery(
                id="read-1",
                type="fact",
                difficulty="easy",
                question="信息熵是什么？",
                corpus_pages=("concepts/信息熵",),
                reference_answer="熵衡量不确定性。",
                key_points=("熵衡量不确定性", "信息熵的定义"),
                expected_trace=ExpectedTrace(
                    tools_any=("wiki_read",),
                    wait="absent",
                    max_tool_rounds=2,
                    no_error=True,
                ),
                forbidden=("编造",),
                raw_sources=(),
                notes="",
            )
            set_ = GoldenSet(
                root=root,
                corpus=corpus,
                queries=(case,),
                manifest={},
                counts={},
                corpus_sha256="c",
                queries_sha256="q",
            )
            model = FauxModelClient(
                [
                    ModelStartEvent("faux"),
                    ModelToolCallEnd(
                        ToolCall(
                            "c1",
                            "wiki_read",
                            '{"slug": "concepts/信息熵"}',
                        )
                    ),
                    ModelDoneEvent(
                        AssistantMessage(stop_reason="tool_calls")
                    ),
                ],
                [
                    ModelStartEvent("faux"),
                    ModelTextDelta("熵衡量不确定性。"),
                    ModelDoneEvent(
                        AssistantMessage(
                            blocks=(TextBlock("熵衡量不确定性。"),),
                            stop_reason="stop",
                        )
                    ),
                ],
            )
            judge = FakeJudge((5, 5, 5))

            run = run_variant(
                set_,
                CONFIG,
                root / "run",
                variant="base",
                model=model,
                judge_client=judge,
            )
            query = run.queries[0]

            self.assertTrue(query.trace_passed)
            self.assertIn("wiki_read", query.evidence.tools)
            self.assertEqual(
                query.evidence.read_pages, ("concepts/信息熵",)
            )
            prompt = "\n".join(
                message.content
                for call in judge.calls
                for message in call["messages"]
            )
            self.assertIn("熵衡量不确定性。", prompt)


class PairTests(unittest.TestCase):
    def _run(self, root: Path, variant: str, scores):
        golden = tiny_golden(root / variant)
        return run_variant(
            golden,
            CONFIG,
            root / variant / "run",
            variant=variant,
            model=FakeModelClient("X 的定义。"),
            judge_client=FakeJudge(scores),
        )

    def test_strict_can_win_the_paired_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._run(root, "base", (3, 3, 3))
            strict = self._run(root, "strict", (5, 5, 5))

            paired = pair_variants(base, strict)

            self.assertEqual(paired.strict_wins, 1)
            self.assertEqual(paired.base_wins, 0)
            self.assertEqual(paired.ties, 0)
            self.assertAlmostEqual(paired.mean_delta, 2.0)
            self.assertAlmostEqual(
                paired.dimension_deltas["grounding"], 2.0
            )
            self.assertEqual(paired.by_type["fact"]["count"], 1)

    def test_base_can_win_and_the_report_shows_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._run(root, "base", (5, 5, 5))
            strict = self._run(root, "strict", (3, 3, 3))
            golden = tiny_golden(root)
            base_record = record_for(base, golden, CONFIG, pair_id="p1")
            strict_record = record_for(
                strict, golden, CONFIG, pair_id="p1"
            )
            paired = pair_variants(base, strict)
            html_path = root / "ab" / "report.html"
            json_path = root / "ab" / "report.json"

            payload = write_ab_report(
                base,
                strict,
                base_record,
                strict_record,
                paired,
                [],
                pair_id="p1",
                json_path=json_path,
                html_path=html_path,
            )
            page = html_path.read_text(encoding="utf-8")

            self.assertEqual(payload["kind"], "ab")
            self.assertEqual(payload["paired"]["baseWins"], 1)
            self.assertIn("base wins", page)
            self.assertIn("strict wins", page)
            self.assertIn("Paired", page)

    def test_the_pair_manifest_names_both_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = tiny_golden(root)
            base = self._run(root, "base", (4, 4, 4))
            strict = self._run(root, "strict", (4, 4, 4))
            records = [
                record_for(base, golden, CONFIG, pair_id="p1"),
                record_for(strict, golden, CONFIG, pair_id="p1"),
            ]

            payload = ab_manifest_payload(
                records, pair_id="p1", started_at=records[0].started_at
            )

            self.assertEqual(payload["kind"], "ab")
            self.assertEqual(
                [item["variant"] for item in payload["variants"]],
                ["base", "strict"],
            )


class ManifestTests(unittest.TestCase):
    def test_public_base_url_drops_userinfo_and_query(self) -> None:
        self.assertEqual(
            public_base_url("https://user:pass@model.test/v1?x=1#y"),
            "https://model.test/v1",
        )
        self.assertEqual(public_base_url("not a url"), "")

    def test_a_missing_score_is_an_empty_history_cell(self) -> None:
        record = RunRecord(
            run_id="r1",
            started_at="2026-11-12T00:00:00Z",
            variant="base",
            pair_id="",
            agent_model="m",
            agent_base_url="https://model.test/v1",
            agent_temperature=0.0,
            judge_model="m",
            judge_temperature=0.0,
            corpus_sha256="c",
            queries_sha256="q",
            prompt_sha256="p",
            judge_prompt_sha256="j",
            git_commit="",
            duration_s=1.0,
            queries=(
                QueryRecord(
                    id="x",
                    type="fact",
                    difficulty="easy",
                    passed=False,
                    trace_passed=True,
                    judge_ok=False,
                ),
            ),
        )

        row = history_row(record)

        self.assertEqual(row["grounding_mean"], "")
        self.assertEqual(row["judge_errors"], 1)
        self.assertEqual(manifest_payload(record)["counts"]["judgeErrors"], 1)


class CliEncodingTests(unittest.TestCase):
    def test_the_cli_summary_uses_ascii_placeholders(self) -> None:
        from evals.golden.__main__ import _means_text

        self.assertEqual(_means_text({"grounding": None}), "g=-")

    def test_the_cli_module_has_no_non_ascii_glyphs(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "evals"
            / "golden"
            / "__main__.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("−", source)
        self.assertNotIn("—", source)


if __name__ == "__main__":
    unittest.main()
