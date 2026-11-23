"""Run the V4 atoms and report what each one did.

An atom is one capability the agent is built from, exercised end to end through
the same HTTP/SSE path the product uses. The local atoms run scripts and cost
nothing; the live smoke reaches the network only when asked. The report keeps
every check, its time and its token count, with the live smoke reported apart; a
failing atom also keeps its project, sessions and event logs under the failure
library, and a passing atom leaves no failure behind.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from evals.harness import CaseRun
from evals.report import Check, utc_now
from evals.support import remove_tree

from noteagent.agent.model import ModelClient, ModelUsageEvent

#: The V4 report schema. The shared ``REPORT_VERSION`` names the V1-V3 report,
#: whose field set V4 extends with tokens and failure artifacts.
V4_REPORT_VERSION = 4


@dataclass(slots=True)
class UsageCounter:
    """The token counts a run's model streams report.

    The local atoms are scripted and report none; a live atom fills these the
    same way, so the report has one shape for both.
    """

    tokens_in: int = 0
    tokens_out: int = 0

    def wrap(self, model: ModelClient) -> ModelClient:
        return _RecordingModel(model, self)


class _RecordingModel:
    def __init__(self, model: ModelClient, counter: UsageCounter) -> None:
        self.model = model
        self.counter = counter

    def stream(self, request):
        for event in self.model.stream(request):
            if isinstance(event, ModelUsageEvent):
                self.counter.tokens_in += event.usage.input_tokens
                self.counter.tokens_out += event.usage.output_tokens
            yield event


@dataclass(frozen=True, slots=True)
class AtomCase:
    """One capability, its seed, and the script that exercises it."""

    name: str
    title: str
    run: Callable[[CaseRun, UsageCounter], None]
    #: An empty tuple starts with no wiki pages; ``None`` would seed the
    #: harness default pages, which an atom must not inherit by accident.
    pages: tuple[tuple[str, str, str], ...] = ()
    #: A live atom reads the user's own settings; a local one stays isolated.
    share_settings: bool = False


@dataclass(frozen=True, slots=True)
class AtomResult:
    """One atom's checks, cost and evidence."""

    name: str
    title: str
    checks: tuple[Check, ...]
    duration_s: float
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""
    #: Where the failure library kept this atom's project, when it failed.
    artifacts: str = ""

    @property
    def passed(self) -> bool:
        return (
            not self.error
            and bool(self.checks)
            and all(check.passed for check in self.checks)
        )

    @property
    def failed_checks(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    @property
    def error_line(self) -> str:
        lines = self.error.strip().splitlines()
        return lines[-1] if lines else ""


@dataclass(frozen=True, slots=True)
class V4Report:
    """The atoms, their checks, and where a failure can be read.

    ``atoms`` is the free local set; ``live`` is the web smoke, reported on its
    own because it can fail for reasons the machine does not own.
    """

    atoms: tuple[AtomResult, ...]
    run_id: str
    started_at: str
    duration_s: float
    live: tuple[AtomResult, ...] = ()

    @property
    def all_atoms(self) -> tuple[AtomResult, ...]:
        return (*self.atoms, *self.live)

    @property
    def passed_count(self) -> int:
        return sum(1 for atom in self.all_atoms if atom.passed)

    @property
    def failed_count(self) -> int:
        return len(self.all_atoms) - self.passed_count

    @property
    def local_passed_count(self) -> int:
        return sum(1 for atom in self.atoms if atom.passed)

    @property
    def live_passed_count(self) -> int:
        return sum(1 for atom in self.live if atom.passed)

    @property
    def tokens_in(self) -> int:
        return sum(atom.tokens_in for atom in self.all_atoms)

    @property
    def tokens_out(self) -> int:
        return sum(atom.tokens_out for atom in self.all_atoms)

    def payload(self) -> dict[str, object]:
        return {
            "version": V4_REPORT_VERSION,
            "setVersion": "v4",
            "runId": self.run_id,
            "startedAt": self.started_at,
            "durationSeconds": round(self.duration_s, 3),
            "passed": self.passed_count,
            "failed": self.failed_count,
            "localPassed": self.local_passed_count,
            "localTotal": len(self.atoms),
            "livePassed": self.live_passed_count,
            "liveTotal": len(self.live),
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "atoms": [_atom_payload(atom) for atom in self.atoms],
            "live": [_atom_payload(atom) for atom in self.live],
        }

    def markdown(self) -> str:
        lines = [
            "# V4 quality eval",
            "",
            f"- Run: {self.run_id}",
            f"- Started: {self.started_at}",
            (
                f"- Local atoms: **{self.local_passed_count} passed, "
                f"{len(self.atoms) - self.local_passed_count} failed**"
            ),
        ]
        if self.live:
            lines.append(
                f"- Live smoke: **{self.live_passed_count} passed, "
                f"{len(self.live) - self.live_passed_count} failed**"
            )
        lines.extend(
            [
                (
                    f"- Total: {self.passed_count} passed, "
                    f"{self.failed_count} failed in {self.duration_s:.1f}s"
                ),
                f"- Tokens in/out: {self.tokens_in}/{self.tokens_out}",
            ]
        )
        lines.extend(_table("Local atoms", self.atoms))
        if self.live:
            lines.extend(_table("Live smoke", self.live))
        failures = [
            atom for atom in self.all_atoms if not atom.passed
        ]
        if failures:
            lines.extend(["", "## Failures"])
            for atom in failures:
                lines.extend(["", f"### `{atom.name}`", "", atom.title])
                for check in atom.failed_checks:
                    lines.append(f"- {check.name}: {check.detail}")
                if atom.error:
                    lines.extend(
                        ["", "```", atom.error_line, "```"]
                    )
                if atom.artifacts:
                    lines.append(f"- artifacts: {atom.artifacts}")
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload(), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        path.with_suffix(".md").write_text(
            self.markdown(), encoding="utf-8"
        )
        return path


RunAtom = Callable[[AtomCase, Path, str], AtomResult]


def run_atoms(
    atoms: tuple[AtomCase, ...],
    failures_root: Path,
    *,
    live: tuple[AtomCase, ...] = (),
    run_id: str | None = None,
    run_atom: RunAtom | None = None,
) -> V4Report:
    """Run every atom once; *run_atom* is the test seam.

    The local atoms run first, then the live smoke. The failure library is not
    cleared: a run adds its own folder, and an older failure stays readable.
    """
    started_at = utc_now()
    identifier = run_id or new_run_id()
    runner: RunAtom = run_atom or run_atom_once
    started = time.monotonic()
    results = tuple(
        runner(atom, Path(failures_root), identifier) for atom in atoms
    )
    live_results = tuple(
        runner(atom, Path(failures_root), identifier) for atom in live
    )
    return V4Report(
        atoms=results,
        run_id=identifier,
        started_at=started_at,
        duration_s=time.monotonic() - started,
        live=live_results,
    )


def run_atom_once(
    atom: AtomCase, failures_root: Path, run_id: str
) -> AtomResult:
    """Run one atom in its own temporary project, keeping a failure's evidence."""
    started = time.monotonic()
    tmp = Path(
        tempfile.mkdtemp(prefix=f"noteagent-v4-{atom.name}-")
    )
    run: CaseRun | None = None
    counter = UsageCounter()
    error = ""
    try:
        run = CaseRun(
            tmp,
            pages=atom.pages,
            share_settings=atom.share_settings,
        )
        atom.run(run, counter)
    except Exception:
        error = traceback.format_exc()
    result = AtomResult(
        name=atom.name,
        title=atom.title,
        checks=tuple(run.checks) if run is not None else (),
        duration_s=time.monotonic() - started,
        tokens_in=counter.tokens_in,
        tokens_out=counter.tokens_out,
        error=error,
    )
    if not result.passed:
        try:
            target = _preserve(
                run, result, Path(failures_root), run_id
            )
            result = replace(result, artifacts=str(target))
        except Exception as err:  # noqa: BLE001 - evidence is best effort
            result = replace(
                result,
                error=(
                    result.error
                    + f"\nthe failure artifacts could not be kept: {err}"
                ).strip(),
            )
    try:
        if run is not None:
            run.close()
    finally:
        remove_tree(tmp)
    return result


def _preserve(
    run: CaseRun | None,
    result: AtomResult,
    failures_root: Path,
    run_id: str,
) -> Path:
    """Keep what a failed atom needs: project, event logs and its checks."""
    target = failures_root / run_id / result.name
    payload: dict[str, object] = {
        "atom": result.name,
        "title": result.title,
        "passed": result.passed,
        "checks": [check.to_payload() for check in result.checks],
        "error": result.error,
        "durationSeconds": round(result.duration_s, 3),
        "tokensIn": result.tokens_in,
        "tokensOut": result.tokens_out,
        "log": list(run.log) if run is not None else [],
        "artifacts": str(target),
    }
    preserve_case(run, target, payload)
    if run is not None and run.log:
        (target / "atom.log").write_text(
            "\n".join(run.log) + "\n", encoding="utf-8"
        )
    return target


def preserve_case(
    run: CaseRun | None,
    target: Path,
    payload: dict[str, object],
) -> Path:
    """Keep a failed case's project, event logs and payload under *target*."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if run is not None:
        run.preserve(target)
    (target / "failure.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _atom_payload(atom: AtomResult) -> dict[str, object]:
    return {
        "name": atom.name,
        "title": atom.title,
        "passed": atom.passed,
        "durationSeconds": round(atom.duration_s, 3),
        "tokensIn": atom.tokens_in,
        "tokensOut": atom.tokens_out,
        "checks": [check.to_payload() for check in atom.checks],
        "error": atom.error_line,
        "artifacts": atom.artifacts,
    }


def _table(title: str, atoms: tuple[AtomResult, ...]) -> list[str]:
    lines = [
        "",
        f"## {title}",
        "",
        "| Atom | Result | Checks | Time | Tokens in/out |",
        "|---|---|---|---|---|",
    ]
    for atom in atoms:
        verdict = "PASS" if atom.passed else "FAIL"
        lines.append(
            f"| `{atom.name}` | {verdict} | "
            f"{len(atom.checks) - len(atom.failed_checks)}/"
            f"{len(atom.checks)} | {atom.duration_s:.1f}s | "
            f"{atom.tokens_in}/{atom.tokens_out} |"
        )
    return lines


def new_run_id() -> str:
    moment = datetime.now(timezone.utc)
    return moment.strftime("%Y%m%dT%H%M%S%fZ")


__all__ = [
    "AtomCase",
    "AtomResult",
    "RunAtom",
    "UsageCounter",
    "V4Report",
    "V4_REPORT_VERSION",
    "new_run_id",
    "preserve_case",
    "run_atom_once",
    "run_atoms",
]
