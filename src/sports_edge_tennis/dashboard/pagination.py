"""Small, framework-light pagination helper for the dashboard's long tables.

Keeps slicing math in one place so the Model Picks, Value Bet, and Value pages
cannot drift. Deterministic: it only ever slices the already-ordered input.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import urlencode

from starlette.requests import Request

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

T = TypeVar("T")


def _compact_pages(page: int, total_pages: int, *, window: int = 1) -> tuple[int, ...]:
    """A small, deterministic page window that always includes first and last."""
    if total_pages <= 1:
        return (1,)
    pages = {1, total_pages}
    for offset in range(-window, window + 1):
        candidate = page + offset
        if 1 <= candidate <= total_pages:
            pages.add(candidate)
    if page <= window + 1:
        pages.update(range(1, min(total_pages, 2 * window + 2) + 1))
    if page >= total_pages - window:
        pages.update(range(max(1, total_pages - (2 * window + 1)), total_pages + 1))
    return tuple(sorted(pages))


@dataclass(frozen=True)
class Pagination:
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    previous_page: int | None
    next_page: int | None
    start_item: int
    end_item: int
    page_numbers: tuple[int, ...]

    @property
    def is_paginated(self) -> bool:
        return self.total_pages > 1

    @property
    def range_text(self) -> str:
        if self.total_items == 0:
            return "No rows"
        return f"Showing {self.start_item}\u2013{self.end_item} of {self.total_items}"


def paginate(
    items: Sequence[T],
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[tuple[T, ...], Pagination]:
    """Slice ``items`` into one page plus its metadata.

    ``page`` is clamped into ``[1, total_pages]``; an out-of-range page therefore
    resolves to the nearest real page instead of an empty result. A zero-item set
    is a single, cleanly renderable page.
    """
    rows = tuple(items)
    total = len(rows)

    size = page_size if isinstance(page_size, int) else DEFAULT_PAGE_SIZE
    size = max(1, min(MAX_PAGE_SIZE, size))

    total_pages = max(1, (total + size - 1) // size)

    current = page if isinstance(page, int) else 1
    current = max(1, min(total_pages, current))

    start_index = (current - 1) * size
    end_index = min(start_index + size, total)
    page_rows = rows[start_index:end_index]

    has_previous = current > 1
    has_next = current < total_pages

    return (
        page_rows,
        Pagination(
            page=current,
            page_size=size,
            total_items=total,
            total_pages=total_pages,
            has_previous=has_previous,
            has_next=has_next,
            previous_page=current - 1 if has_previous else None,
            next_page=current + 1 if has_next else None,
            start_item=start_index + 1 if total else 0,
            end_item=end_index if total else 0,
            page_numbers=_compact_pages(current, total_pages),
        ),
    )


def build_page_url(
    request: Request, param: str, page: int, **overrides: object
) -> str:
    """URL for one table's page, preserving every other existing query param.

    Values equal to the default (``1``) are dropped so URLs stay clean. Unrelated
    filters and the other tables' page params are preserved untouched.
    """
    merged: dict[str, object] = dict(request.query_params)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    merged[param] = page

    cleaned: dict[str, str] = {}
    for key, value in merged.items():
        if value is None:
            continue
        text = str(value)
        if text in ("", "1"):
            continue
        cleaned[key] = text
    query = urlencode(cleaned)
    if query:
        return f"{request.url.path}?{query}"
    return request.url.path


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "Pagination",
    "build_page_url",
    "paginate",
]
