"""Job state machine (AGENT_DESIGN.md §7). The only place transitions are decided."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
PROCESSING = {
    JobStatus.PREPROCESSING,
    JobStatus.PARSING,
    JobStatus.EXTRACTING,
    JobStatus.VALIDATING,
    JobStatus.REPAIRING,
}

_ALLOWED: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.PREPROCESSING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.PREPROCESSING: {JobStatus.PARSING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED},
    JobStatus.PARSING: {JobStatus.EXTRACTING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED},
    JobStatus.EXTRACTING: {JobStatus.VALIDATING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED},
    JobStatus.VALIDATING: {
        JobStatus.REPAIRING,
        JobStatus.COMPLETED,
        JobStatus.REVIEW_REQUIRED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.QUEUED,
    },
    JobStatus.REPAIRING: {JobStatus.VALIDATING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED},
    JobStatus.REVIEW_REQUIRED: {JobStatus.VALIDATING, JobStatus.CANCELLED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: {JobStatus.QUEUED},  # only via explicit retry
    JobStatus.CANCELLED: set(),
}


class InvalidTransition(Exception):
    def __init__(self, current: JobStatus, target: JobStatus):
        super().__init__(f"illegal job transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: JobStatus | str, target: JobStatus | str) -> bool:
    return JobStatus(target) in _ALLOWED[JobStatus(current)]


def check_transition(current: JobStatus | str, target: JobStatus | str) -> JobStatus:
    cur, tgt = JobStatus(current), JobStatus(target)
    if tgt not in _ALLOWED[cur]:
        raise InvalidTransition(cur, tgt)
    return tgt
