from __future__ import annotations


class ServiceError(Exception):
    """Base class for failures raised by the service layer."""


class ProviderFetchError(ServiceError):
    """A provider could not deliver data.

    Raised instead of returning an empty result so that callers can never
    mistake a source failure for "no transactions happened".
    """

    def __init__(self, *, provider: str, subject_id: str, message: str) -> None:
        super().__init__(f"provider {provider} failed for {subject_id}: {message}")
        self.provider = provider
        self.subject_id = subject_id


class ProviderContractError(ServiceError):
    """A provider answered with data that does not belong to what was asked for.

    This is a defect in the provider or in the request wiring, not a property of
    an individual record, so it is raised rather than reported as a rejection: a
    batch that answers the wrong question must not be partially persisted.
    """

    def __init__(self, *, provider: str, subject_id: str, message: str) -> None:
        super().__init__(f"provider {provider} broke its contract for {subject_id}: {message}")
        self.provider = provider
        self.subject_id = subject_id
