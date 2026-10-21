"""Kho lưu trạng thái trong bộ nhớ, an toàn đa luồng, bản ghi bất biến."""

from __future__ import annotations  # Store.list che kiểu list trong thân class

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Callable, Generic, TypeVar


class Status(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    MERGING = "merging"
    DONE = "done"
    ERROR = "error"

    @property
    def is_finished(self) -> bool:
        return self in (Status.DONE, Status.ERROR)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class Job:
    id: str
    url: str
    source: str
    audio_format: str
    bitrate: int
    owner: str = ""
    status: Status = Status.QUEUED
    progress: float = 0.0
    title: str | None = None
    duration: float | None = None
    file_path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_public(self) -> dict:
        data = asdict(self)
        data.pop("file_path")
        data.pop("owner")
        data["status"] = self.status.value
        data["has_file"] = self.status is Status.DONE and self.file_path is not None
        return data


@dataclass(frozen=True)
class Merge:
    id: str
    name: str
    job_ids: tuple[str, ...]
    audio_format: str
    bitrate: int
    gap_seconds: float
    owner: str = ""
    status: Status = Status.QUEUED
    file_path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_public(self) -> dict:
        data = asdict(self)
        data.pop("file_path")
        data.pop("owner")
        data["job_ids"] = list(self.job_ids)
        data["status"] = self.status.value
        data["has_file"] = self.status is Status.DONE and self.file_path is not None
        return data


T = TypeVar("T", Job, Merge)


class Store(Generic[T]):
    def __init__(self) -> None:
        self._items: dict[str, T] = {}
        self._lock = threading.Lock()

    def add(self, item: T) -> T:
        with self._lock:
            self._items[item.id] = item
        return item

    def get(self, item_id: str) -> T | None:
        with self._lock:
            return self._items.get(item_id)

    def list(self) -> list[T]:
        with self._lock:
            return sorted(self._items.values(), key=lambda i: i.created_at)

    def update(self, item_id: str, **changes) -> T | None:
        with self._lock:
            current = self._items.get(item_id)
            if current is None:
                return None
            updated = replace(current, **changes)
            self._items[item_id] = updated
            return updated

    def update_if(self, item_id: str, predicate: Callable[[T], bool], **changes) -> T | None:
        """Kiểm tra và cập nhật trong cùng một lần khoá (tránh race check-then-act)."""
        with self._lock:
            current = self._items.get(item_id)
            if current is None or not predicate(current):
                return None
            updated = replace(current, **changes)
            self._items[item_id] = updated
            return updated

    def count(self, predicate: Callable[[T], bool]) -> int:
        with self._lock:
            return sum(1 for item in self._items.values() if predicate(item))

    def list_where(self, predicate: Callable[[T], bool]) -> list[T]:
        return [item for item in self.list() if predicate(item)]

    def remove_if(self, item_id: str, predicate: Callable[[T], bool]) -> T | None:
        with self._lock:
            current = self._items.get(item_id)
            if current is None or not predicate(current):
                return None
            return self._items.pop(item_id)

    def remove(self, item_id: str) -> T | None:
        with self._lock:
            return self._items.pop(item_id, None)
