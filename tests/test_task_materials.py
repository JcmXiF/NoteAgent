"""The task's material is typed; the stored shape and views do not move."""

import unittest

from noteagent.task.model import (
    Material,
    material_from_dict,
    material_to_dict,
    material_view,
    task_from_dict,
    task_to_dict,
)


def stored_material() -> dict[str, object]:
    """A material row as the current task file stores it."""
    return {
        "id": "raw/web/entropy-a1.html",
        "title": "Entropy",
        "url": "https://example.com/entropy",
        "kind": "html",
        "fidelity": "ok",
        "state": "staged",
        "runId": "task-7",
        "blockCount": 3,
        "blockIds": ["b1", "b2", "b3"],
        "blocks": [
            {"id": "b1", "tag": "p", "preview": "熵衡量不确定性。"},
            {"id": "b2", "tag": "p", "preview": "信息是不确定性的减少。"},
        ],
    }


def task_raw(materials: list[object]) -> dict[str, object]:
    return {
        "version": 1,
        "id": "task-7",
        "kind": "writing",
        "intent": "",
        "status": "idle",
        "rev": 0,
        "createdAt": "2026-11-10T09:00:00Z",
        "updatedAt": "2026-11-10T09:00:00Z",
        "cards": [],
        "materials": materials,
        "frames": [],
        "plan": [],
        "ops": [],
    }


class MaterialShapeTests(unittest.TestCase):
    def test_a_stored_material_round_trips_with_the_same_keys(self) -> None:
        material = material_from_dict(stored_material())

        self.assertIsInstance(material, Material)
        self.assertEqual(stored_material(), material_to_dict(material))

    def test_the_bounded_view_is_unchanged(self) -> None:
        material = material_from_dict(stored_material())

        self.assertEqual(
            {
                "id": "raw/web/entropy-a1.html",
                "title": "Entropy",
                "url": "https://example.com/entropy",
                "kind": "html",
                "fidelity": "ok",
                "state": "staged",
                "blockCount": 3,
                "blocks": [
                    {"id": "b1", "tag": "p", "preview": "熵衡量不确定性。"},
                    {"id": "b2", "tag": "p", "preview": "信息是不确定性的减少。"},
                ],
                "blocksOmitted": 1,
            },
            material_view(material),
        )

    def test_an_older_row_without_block_ids_uses_its_preview_index(self) -> None:
        row = stored_material()
        row.pop("blockIds")

        material = material_from_dict(row)

        self.assertEqual(("b1", "b2"), material.block_ids)

    def test_a_material_without_an_id_is_not_readable(self) -> None:
        with self.assertRaises(ValueError):
            material_from_dict({"title": "no id"})


class TaskMaterialTests(unittest.TestCase):
    def test_a_task_round_trip_keeps_the_material_row(self) -> None:
        task = task_from_dict(task_raw([stored_material()]))

        self.assertEqual([stored_material()], task_to_dict(task)["materials"])

    def test_a_bad_material_row_is_skipped_and_the_task_stays_readable(self) -> None:
        task = task_from_dict(
            task_raw([stored_material(), {"title": "no id"}, "nope"])
        )

        self.assertEqual(1, len(task.materials))
        self.assertEqual("raw/web/entropy-a1.html", task.materials[0].id)


if __name__ == "__main__":
    unittest.main()
