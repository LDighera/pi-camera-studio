"""Small state helpers shared by GUI lifecycle code and headless tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeferredClose:
    """Remember a close request until all asynchronous camera jobs finish."""

    requested: bool = False

    def defer_if_busy(self, *camera_jobs: object | None) -> bool:
        busy = any(job is not None for job in camera_jobs)
        if busy:
            self.requested = True
        return busy

    def consume_if_idle(self, *camera_jobs: object | None) -> bool:
        if not self.requested or any(job is not None for job in camera_jobs):
            return False
        self.requested = False
        return True

    def cancel(self) -> None:
        self.requested = False
