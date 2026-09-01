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
