"""Stable domain failures; adapters may map ``code`` to HTTP responses."""

from __future__ import annotations


class LeoFTGenerationError(Exception):
    code = "leoft_generation_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class DomainValidationError(LeoFTGenerationError):
    code = "domain_validation_error"


class ResourceNotFoundError(LeoFTGenerationError):
    code = "resource_not_found"


class IdempotencyConflictError(LeoFTGenerationError):
    code = "idempotency_mismatch"


class OptimisticLockError(LeoFTGenerationError):
    code = "stale_version"


class InvalidJobTransitionError(LeoFTGenerationError):
    code = "invalid_job_transition"


class InvalidTaskTransitionError(LeoFTGenerationError):
    code = "invalid_task_transition"


class LeaseConflictError(LeoFTGenerationError):
    code = "lease_not_owned"


class AllowlistDeniedError(LeoFTGenerationError):
    code = "e2e_allowlist_denied"


class AttemptsExhaustedError(LeoFTGenerationError):
    code = "maximum_attempts_exhausted"
