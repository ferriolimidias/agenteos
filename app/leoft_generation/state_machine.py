"""Pure transition rules frozen by the LeoFT E2E contract."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from .enums import GenerationJobStatus, GenerationTaskStatus
from .errors import InvalidJobTransitionError, InvalidTaskTransitionError

JOB_TRANSITIONS: Mapping[GenerationJobStatus, AbstractSet[GenerationJobStatus]] = {
    GenerationJobStatus.PENDING: {
        GenerationJobStatus.DISPATCHING,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.DISPATCHING: {
        GenerationJobStatus.PLANNING,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.PLANNING: {
        GenerationJobStatus.GENERATING,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.GENERATING: {
        GenerationJobStatus.VALIDATING,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.VALIDATING: {
        GenerationJobStatus.AWAITING_REVIEW,
        GenerationJobStatus.REPAIRING,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.REPAIRING: {
        GenerationJobStatus.GENERATING,
        GenerationJobStatus.VALIDATING,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.AWAITING_REVIEW: {
        GenerationJobStatus.APPROVED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.APPROVED: {
        GenerationJobStatus.PUBLISHING,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.PUBLISHING: {
        GenerationJobStatus.PUBLISHED,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.FAILED: set(),
    GenerationJobStatus.PUBLISHED: set(),
    GenerationJobStatus.CANCELLED: set(),
}


TASK_TRANSITIONS: Mapping[GenerationTaskStatus, AbstractSet[GenerationTaskStatus]] = {
    GenerationTaskStatus.PENDING: {
        GenerationTaskStatus.GENERATING,
        GenerationTaskStatus.CANCELLED,
    },
    GenerationTaskStatus.GENERATING: {
        GenerationTaskStatus.PENDING,
        GenerationTaskStatus.VALIDATING,
        GenerationTaskStatus.FAILED,
        GenerationTaskStatus.CANCELLED,
    },
    GenerationTaskStatus.VALIDATING: {
        GenerationTaskStatus.PENDING,
        GenerationTaskStatus.COMPLETED,
        GenerationTaskStatus.REPAIRING,
        GenerationTaskStatus.FAILED,
        GenerationTaskStatus.CANCELLED,
    },
    GenerationTaskStatus.REPAIRING: {
        GenerationTaskStatus.PENDING,
        GenerationTaskStatus.GENERATING,
        GenerationTaskStatus.FAILED,
        GenerationTaskStatus.CANCELLED,
    },
    GenerationTaskStatus.COMPLETED: set(),
    GenerationTaskStatus.FAILED: set(),
    GenerationTaskStatus.CANCELLED: set(),
}


def validate_job_transition(
    current: GenerationJobStatus | str, target: GenerationJobStatus | str
) -> GenerationJobStatus:
    current_status = GenerationJobStatus(current)
    target_status = GenerationJobStatus(target)
    if target_status not in JOB_TRANSITIONS[current_status]:
        raise InvalidJobTransitionError(
            f"job transition {current_status.value}->{target_status.value} is not allowed"
        )
    return target_status


def validate_task_transition(
    current: GenerationTaskStatus | str, target: GenerationTaskStatus | str
) -> GenerationTaskStatus:
    current_status = GenerationTaskStatus(current)
    target_status = GenerationTaskStatus(target)
    if target_status not in TASK_TRANSITIONS[current_status]:
        raise InvalidTaskTransitionError(
            f"task transition {current_status.value}->{target_status.value} is not allowed"
        )
    return target_status
