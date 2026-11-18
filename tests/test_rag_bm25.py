"""BM25F lexical retrieval: the tokenizer, ranking, and the exact tier."""

from __future__ import annotations

import unittest

from noteagent.rag.bm25 import (
    Bm25Index,
    Bm25Params,
    LexicalDoc,
    tokenize,
)


def doc(
    chunk_id: str,
    page_id: str,
    text: str,
    *,
    title: str = "",
    heading: str = "",
    kind: str = "wiki",
    index: int = 0,
) -> LexicalDoc:
    return LexicalDoc(
        chunk_id=chunk_id,
        page_id=page_id,
        kind=kind,
        chunk_index=index,
        chunk_text=text,
        heading_path=heading,
        title=title,
    )


class TokenizerTests(unittest.TestCase):
    def test_cjk_bigrams_and_english_words_are_mixed(self) -> None:
        self.assertEqual(
            tokenize("信息熵 Entropy"), ("信息", "息熵", "entropy")
        )
        self.assertEqual(tokenize("KL 散度"), ("kl", "散度"))

    def test_english_is_casefolded(self) -> None:
        self.assertEqual(tokenize("ENTROPY Qwen3"), ("entropy", "qwen3"))

    def test_accented_letters_are_not_cut_at_the_accent(self) -> None:
        self.assertEqual(
            tokenize("café naïve"), ("café", "naïve")
        )

    def test_a_single_cjk_character_is_its_own_token(self) -> None:
        self.assertEqual(tokenize("熵"), ("熵",))

    def test_punctuation_and_an_empty_string_produce_no_tokens(self) -> None:
        self.assertEqual(tokenize(""), ())
        self.assertEqual(tokenize("  ，。！  "), ())

    def test_whitespace_and_case_do_not_change_the_tokens(self) -> None:
        self.assertEqual(
            tokenize("信息熵  Embedding"),
            tokenize("信息熵\tembedding"),
        )


class ExactMatchTests(unittest.TestCase):
    def test_an_exact_slug_ranks_before_a_strong_bm25_match(self) -> None:
        exact = doc(
            "wiki:concepts/entropy#0",
            "concepts/entropy",
            "entropy",
            title="Entropy",
        )
        noisy = doc(
            "wiki:other#0",
            "other",
            "entropy " * 80,
            title="Other",
        )
        index = Bm25Index([exact, noisy])

        hits = index.search("concepts/entropy")

        self.assertEqual(hits[0].chunk_id, exact.chunk_id)
        self.assertTrue(hits[0].exact)
        self.assertGreater(hits[0].score, 0)

    def test_a_slug_leaf_is_enough(self) -> None:
        exact = doc(
            "wiki:concepts/kl-divergence#0",
            "concepts/kl-divergence",
            "distance",
            title="KL 散度",
        )
        noisy = doc(
            "wiki:other#0",
            "other",
            "divergence " * 20,
        )
        index = Bm25Index([exact, noisy])

        hits = index.search("kl-divergence")

        self.assertEqual(hits[0].chunk_id, exact.chunk_id)
        self.assertTrue(hits[0].exact)

    def test_an_exact_title_ranks_before_a_strong_bm25_match(self) -> None:
        exact = doc(
            "wiki:concepts/kl-divergence#0",
            "concepts/kl-divergence",
            "distance",
            title="KL 散度",
        )
        noisy = doc(
            "wiki:other#0",
            "other",
            "散度 " * 80,
            title="Other",
        )
        index = Bm25Index([exact, noisy])

        hits = index.search("KL 散度")

        self.assertEqual(hits[0].chunk_id, exact.chunk_id)
        self.assertTrue(hits[0].exact)

    def test_one_exact_page_contributes_one_exact_chunk(self) -> None:
        page = [
            doc(
                f"wiki:entropy#{index}",
                "entropy",
                "filler",
                title="Entropy",
                index=index,
            )
            for index in range(60)
        ]
        body_match = doc("wiki:other#0", "other", "entropy " * 10)
        index = Bm25Index([*page, body_match])

        hits = index.search("entropy", limit=50)

        exact = [hit for hit in hits if hit.exact]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].chunk_id, "wiki:entropy#0")
        self.assertIn(
            "wiki:other#0", [hit.chunk_id for hit in hits]
        )

    def test_the_exact_page_contributes_its_best_chunk(self) -> None:
        index = Bm25Index(
            [
                doc(
                    "wiki:entropy#0",
                    "entropy",
                    "filler",
                    title="Entropy",
                    index=0,
                ),
                doc(
                    "wiki:entropy#1",
                    "entropy",
                    "entropy details",
                    title="Entropy",
                    index=1,
                ),
            ]
        )

        hits = index.search("entropy")

        exact = [hit for hit in hits if hit.exact]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].chunk_id, "wiki:entropy#1")

    def test_an_exact_slug_matches_even_with_an_empty_body(self) -> None:
        exact = doc("wiki:a#0", "a", "")
        index = Bm25Index([exact])

        hits = index.search("a")

        self.assertEqual([hit.chunk_id for hit in hits], ["wiki:a#0"])
        self.assertTrue(hits[0].exact)


class RankingTests(unittest.TestCase):
    def test_english_words_are_found_in_the_body(self) -> None:
        index = Bm25Index(
            [doc("wiki:a#0", "a", "Entropy measures uncertainty")]
        )

        hits = index.search("entropy")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].matched, ("body",))

    def test_a_chinese_phrase_ranks_above_a_partial_bigram_match(self) -> None:
        phrase = doc(
            "wiki:info#0", "info", "信息熵衡量不确定性"
        )
        partial = doc("wiki:other#0", "other", "信息在这里")
        index = Bm25Index([phrase, partial])

        hits = index.search("信息熵")

        self.assertEqual(hits[0].chunk_id, phrase.chunk_id)
        self.assertGreater(hits[0].score, hits[1].score)

    def test_a_short_page_outranks_a_long_page_with_one_match(self) -> None:
        short = doc("wiki:s#0", "s", "entropy")
        long = doc(
            "wiki:l#0", "l", "entropy " + "filler " * 100
        )
        index = Bm25Index([short, long])

        hits = index.search("entropy")

        self.assertEqual(hits[0].chunk_id, short.chunk_id)

    def test_more_repetitions_in_a_document_rank_higher(self) -> None:
        many = doc(
            "wiki:many#0", "many", "entropy entropy entropy"
        )
        once = doc("wiki:once#0", "once", "entropy other words")
        index = Bm25Index([many, once])

        hits = index.search("entropy")

        self.assertEqual(hits[0].chunk_id, many.chunk_id)

    def test_repeating_the_query_does_not_change_the_score(self) -> None:
        index = Bm25Index(
            [
                doc("wiki:a#0", "a", "entropy"),
                doc("wiki:b#0", "b", "entropy entropy"),
            ]
        )

        once = index.search("entropy")
        twice = index.search("entropy entropy")

        self.assertEqual(
            [(hit.chunk_id, hit.score) for hit in once],
            [(hit.chunk_id, hit.score) for hit in twice],
        )

    def test_a_page_contributes_only_its_best_chunk(self) -> None:
        index = Bm25Index(
            [
                doc("wiki:a#0", "a", "entropy"),
                doc("wiki:a#1", "a", "entropy entropy entropy"),
            ]
        )

        hits = index.search("entropy")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk_id, "wiki:a#1")

    def test_matched_fields_name_where_the_terms_landed(self) -> None:
        in_title = doc("wiki:t#0", "t", "other", title="entropy")
        in_body = doc("wiki:b#0", "b", "entropy", title="Other")
        in_both = doc("wiki:x#0", "x", "entropy", title="entropy")
        index = Bm25Index([in_title, in_body, in_both])

        hits = {
            hit.chunk_id: hit for hit in index.search("entropy")
        }

        self.assertEqual(hits["wiki:t#0"].matched, ("title",))
        self.assertEqual(hits["wiki:b#0"].matched, ("body",))
        self.assertEqual(
            hits["wiki:x#0"].matched, ("body", "title")
        )


class InterfaceTests(unittest.TestCase):
    def test_an_empty_query_returns_nothing(self) -> None:
        index = Bm25Index([doc("wiki:a#0", "a", "entropy")])

        self.assertEqual(index.search(""), [])
        self.assertEqual(index.search("   "), [])

    def test_the_kind_filter_excludes_notes(self) -> None:
        wiki = doc("wiki:a#0", "a", "entropy", kind="wiki")
        note = doc("note:n#0", "a", "entropy", kind="note")
        index = Bm25Index([wiki, note])

        hits = index.search("entropy", kind="wiki")

        self.assertEqual([hit.kind for hit in hits], ["wiki"])

    def test_the_limit_caps_the_candidates(self) -> None:
        docs = [
            doc(f"wiki:p{index}#0", f"p{index}", "entropy")
            for index in range(10)
        ]
        index = Bm25Index(docs)

        self.assertEqual(len(index.search("entropy", limit=3)), 3)

    def test_a_limit_of_zero_returns_nothing(self) -> None:
        index = Bm25Index([doc("wiki:a#0", "a", "entropy")])

        self.assertEqual(index.search("entropy", limit=0), [])

    def test_the_index_reports_its_size(self) -> None:
        index = Bm25Index(
            [
                doc("wiki:a#0", "a", "one"),
                doc("wiki:b#0", "b", "two"),
            ]
        )

        self.assertEqual(index.size, 2)

    def test_duplicate_chunk_ids_are_refused(self) -> None:
        duplicate = doc("wiki:a#0", "a", "one")

        with self.assertRaises(ValueError):
            Bm25Index([duplicate, duplicate])

    def test_parameters_are_configurable(self) -> None:
        index = Bm25Index(
            [doc("wiki:a#0", "a", "entropy")],
            params=Bm25Params(k1=0.0, b=0.0, exact_bonus=1.0),
        )

        self.assertEqual(len(index.search("entropy")), 1)


class DocTests(unittest.TestCase):
    def test_a_chunk_table_row_becomes_a_lexical_doc(self) -> None:
        row = {
            "chunk_id": "wiki:a#0",
            "page_id": "a",
            "kind": "wiki",
            "chunk_index": 0,
            "chunk_text": "alpha",
            "heading_path": "A",
        }

        lexical = LexicalDoc.from_row(row, title="Alpha")

        self.assertEqual(lexical.chunk_id, "wiki:a#0")
        self.assertEqual(lexical.page_id, "a")
        self.assertEqual(lexical.kind, "wiki")
        self.assertEqual(lexical.chunk_text, "alpha")
        self.assertEqual(lexical.heading_path, "A")
        self.assertEqual(lexical.title, "Alpha")


if __name__ == "__main__":
    unittest.main()
