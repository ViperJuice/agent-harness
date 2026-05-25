from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .launcher import LaunchResult, LaunchSpec, launch_with_spec
from .observability import write_terminal_summary
from .runtime_paths import phase_loop_dir


@dataclass(frozen=True)
class PhaseWorkerJob:
    phase: str
    spec: LaunchSpec
    log_path: Path | None = None
    heartbeat_path: Path | None = None
    dry_run: bool = False
    stream_output: bool = False
    heartbeat_interval_seconds: float = 30.0
    quiet_warning_seconds: float = 300.0
    quiet_blocker_seconds: float = 900.0


@dataclass(frozen=True)
class PhaseWorkerResult:
    phase: str
    result: LaunchResult
    summary_path: Path
    terminal_summary: dict[str, object]


def worker_summary_path(repo: Path, roadmap: Path, phase: str) -> Path:
    return phase_loop_dir(repo) / _roadmap_slug(repo, roadmap) / "workers" / f"{phase}.summary.json"


def run_phase_worker_pool(
    repo: Path,
    roadmap: Path,
    jobs: tuple[PhaseWorkerJob, ...] | list[PhaseWorkerJob],
    *,
    max_workers: int | None = None,
) -> tuple[PhaseWorkerResult, ...]:
    if not jobs:
        return ()
    worker_count = max_workers or min(len(jobs), os.cpu_count() or 1)
    worker_count = max(1, min(worker_count, len(jobs)))
    by_phase = {job.phase: index for index, job in enumerate(jobs)}
    results: list[PhaseWorkerResult] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_run_one, repo, roadmap, job): job for job in jobs}
        for future in as_completed(futures):
            results.append(future.result())
    return tuple(sorted(results, key=lambda item: by_phase[item.phase]))


def _run_one(repo: Path, roadmap: Path, job: PhaseWorkerJob) -> PhaseWorkerResult:
    result = launch_with_spec(
        job.spec,
        dry_run=job.dry_run,
        log_path=job.log_path,
        heartbeat_path=job.heartbeat_path,
        stream_output=job.stream_output,
        heartbeat_interval_seconds=job.heartbeat_interval_seconds,
        quiet_warning_seconds=job.quiet_warning_seconds,
        quiet_blocker_seconds=job.quiet_blocker_seconds,
    )
    summary = {
        "phase": job.phase,
        "terminal_status": "blocked" if result.failed else "complete",
        "verification_status": "blocked" if result.failed else "passed",
        "returncode": result.returncode,
        "executor": result.executor,
        "log_path": result.log_path,
    }
    path = worker_summary_path(repo, roadmap, job.phase)
    write_terminal_summary(path, summary)
    return PhaseWorkerResult(phase=job.phase, result=result, summary_path=path, terminal_summary=summary)


def _roadmap_slug(repo: Path, roadmap: Path) -> str:
    try:
        value = str(roadmap.relative_to(repo))
    except ValueError:
        value = roadmap.name
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value).strip("-") or "roadmap"
