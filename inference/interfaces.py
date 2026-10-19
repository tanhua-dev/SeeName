"""Model operations independent of transport, runtime executable and weight format."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[str, int, int], None]
StopRequested = Callable[[], bool]


@dataclass(frozen=True)
class InferenceResult:
    text: str
    elapsed_seconds: float
    prompt_tokens: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedModel:
    name: str
    size_bytes: int = 0
    device_bytes: int = 0


@dataclass(frozen=True)
class BackendStatus:
    available: bool
    models: tuple[LoadedModel, ...] = ()


@runtime_checkable
class InferenceBackend(Protocol):
    """One shared backend per application, retaining loaded resources between images.

    Call start() before load()/describe(). load() warms the selected model and
    returns (success, error_text); describe() propagates inference errors.
    Timeouts are seconds. start() and close() are safe to repeat. close() releases
    resources owned by this instance, without terminating an unrelated runtime.
    status() reports availability separately from the list of loaded models.
    """

    def start(self, on_log: LogCallback | None = None, timeout: float = 120) -> bool: ...

    def load(self, model: str, timeout: float = 180) -> tuple[bool, str]: ...

    def describe(self, image: bytes, *, model: str, prompt: str,
                 timeout: float = 300, max_tokens: int = 64) -> InferenceResult: ...

    def status(self) -> BackendStatus: ...

    def close(self) -> None: ...


@runtime_checkable
class ModelStore(Protocol):
    """Model discovery, configuration and cancellable downloads for one backend.

    installed() maps model identifiers to bytes on disk. directory(create=False)
    only resolves the configured location. set_directory() synchronizes the
    backend with that location, without moving existing weights. A download
    reports progress and checks cancellation at provider progress boundaries;
    provider/download errors propagate to the caller. Initialization required
    by a provider uses the paired InferenceBackend.start().
    """

    @property
    def catalog(self) -> list[dict[str, str]]: ...

    def directory(self, create: bool = True) -> str: ...

    def set_directory(self, path: str, on_log: LogCallback | None = None) -> bool: ...

    def installed(self, retry: int = 2) -> dict[str, int]: ...

    def exists(self, name: str, installed: dict[str, int] | None = None) -> bool: ...

    def download(self, name: str, on_progress: ProgressCallback | None = None,
                 should_stop: StopRequested | None = None) -> str: ...

    def source(self) -> str: ...

    def set_source(self, url: str) -> str: ...
