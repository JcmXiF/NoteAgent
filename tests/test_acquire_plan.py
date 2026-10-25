from __future__ import annotations

import unittest

from noteagent.acquire.budget import (
    MAX_CANDIDATES,
    MAX_FETCHES,
    MAX_WEB_SEARCHES,
    MAX_WIKIPEDIA_LOOKUPS,
)
from noteagent.acquire.plan import format_search_plan, open_search_plan
from noteagent.acquire.search_config import SearchConfig
from noteagent.acquire.skills import SkillNotFoundError, list_skills, load_skill
from noteagent.acquire.tools.web_search import web_search
from noteagent.acquire.tools.wikipedia import wikipedia_lookup
from noteagent.acquire.types import ProviderNotConfiguredError


BODY_ONLY_ENCYCLOPEDIA = "定义缺口才调用 wikipedia_lookup"
BODY_ONLY_SEARCH = "不要规定必须 N 篇讲解"


class AcquirePlanTests(unittest.TestCase):
    def test_first_query_is_the_topic(self) -> None:
        plan = open_search_plan("  KL散度  ")
        self.assertEqual(plan.topic, "KL散度")
        self.assertEqual(plan.first_query, "KL散度")
        self.assertIsNone(plan.intent)

    def test_intent_is_optional(self) -> None:
        plan = open_search_plan("KL散度", intent="  要推导  ")
        self.assertEqual(plan.intent, "要推导")

    def test_empty_topic_rejected(self) -> None:
        with self.assertRaises(ValueError):
            open_search_plan("   ")

    def test_budget_matches_the_caps(self) -> None:
        plan = open_search_plan("KL散度")
        self.assertEqual(plan.budget.max_web_searches, MAX_WEB_SEARCHES)
        self.assertEqual(plan.budget.max_wikipedia_lookups, MAX_WIKIPEDIA_LOOKUPS)
        self.assertEqual(plan.budget.max_candidates, MAX_CANDIDATES)
        self.assertEqual(plan.budget.max_fetches, MAX_FETCHES)
        self.assertEqual(MAX_WEB_SEARCHES, 3)
        self.assertEqual(MAX_WIKIPEDIA_LOOKUPS, 1)
        self.assertEqual(MAX_CANDIDATES, 8)
        self.assertEqual(MAX_FETCHES, 8)

    def test_tool_catalog(self) -> None:
        plan = open_search_plan(
            "KL散度",
            search_config=SearchConfig(provider="none", api_key=""),
        )
        names = [spec.name for spec in plan.tools]
        self.assertEqual(names, ["web_search", "web_fetch", "wikipedia_lookup"])
        self.assertFalse(plan.tools[0].configured)
        self.assertTrue(plan.tools[1].configured)
        self.assertFalse(plan.tools[2].configured)

    def test_skill_catalog_hides_bodies(self) -> None:
        catalog = list_skills()
        names = {entry.name for entry in catalog}
        self.assertEqual(names, {"search-for-notes", "encyclopedia-gap"})
        blob = "\n".join(entry.description for entry in catalog)
        self.assertNotIn(BODY_ONLY_ENCYCLOPEDIA, blob)
        self.assertNotIn(BODY_ONLY_SEARCH, blob)

    def test_load_skill_returns_body(self) -> None:
        skill = load_skill("encyclopedia-gap")
        self.assertIn(BODY_ONLY_ENCYCLOPEDIA, skill.body)
        search = load_skill("search-for-notes")
        self.assertIn(BODY_ONLY_SEARCH, search.body)

    def test_unknown_skill_raises(self) -> None:
        with self.assertRaises(SkillNotFoundError):
            load_skill("not-a-skill")

    def test_plan_can_load_one_skill(self) -> None:
        plan = open_search_plan(
            "KL散度",
            load_skill_names=("encyclopedia-gap",),
            search_config=SearchConfig(provider="none", api_key=""),
        )
        self.assertEqual(len(plan.loaded_skills), 1)
        self.assertEqual(plan.loaded_skills[0].name, "encyclopedia-gap")
        text = format_search_plan(plan)
        self.assertIn(BODY_ONLY_ENCYCLOPEDIA, text)
        self.assertIn("not configured", text)

    def test_unconfigured_providers_do_not_search_the_network(self) -> None:
        with self.assertRaises(ProviderNotConfiguredError) as web:
            web_search(
                "KL散度",
                config=SearchConfig(provider="none", api_key=""),
            )
        self.assertIn("not configured", str(web.exception))
        with self.assertRaises(ProviderNotConfiguredError) as wiki:
            wikipedia_lookup("KL散度")
        self.assertIn("no encyclopedia provider", str(wiki.exception))


if __name__ == "__main__":
    unittest.main()
