"""Record selection shared by the command line and the web UI.

Both surfaces turn their own inputs (argparse flags, a submitted form) into a
:class:`Selection`, so the order of the steps (filter, sort, limit) and the
meaning of each filter live in one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .formatting import filter_records_by_metadata, sort_records
from .models import PromptRecord

# The catalogs a split run writes.
SPLIT_MODES = ("all", "text", "image")


@dataclass(frozen=True)
class Selection:
    """Which records to keep, and in what order.

    ``price`` is ``"all"``, ``"free"``, or ``"paid"``. The created bounds are
    epoch milliseconds, inclusive.
    """

    domains: frozenset[str] = frozenset()
    prompt_types: frozenset[str] = frozenset()
    price: str = "all"
    min_price: float | None = None
    max_price: float | None = None
    since_created: int | None = None
    until_created: int | None = None
    min_sales: int | None = None
    min_rating: float | None = None
    sort: str = "newest"
    limit: int | None = None

    def apply(self, records: Sequence[PromptRecord]) -> list[PromptRecord]:
        """Filter, then sort, then limit. An empty result means nothing matched."""
        selected = filter_records_by_metadata(
            list(records),
            domains=set(self.domains),
            prompt_types=set(self.prompt_types),
            free_only=self.price == "free",
            paid_only=self.price == "paid",
            min_price=self.min_price,
            max_price=self.max_price,
            since_created=self.since_created,
            until_created=self.until_created,
            min_sales=self.min_sales,
            min_rating=self.min_rating,
        )
        selected = sort_records(selected, self.sort)
        if self.limit is not None:
            selected = selected[: self.limit]
        return selected


def split_modes(mode: str) -> tuple[str, ...]:
    """The catalogs to write for ``mode``: all three for ``split``, else just one."""
    return SPLIT_MODES if mode == "split" else (mode,)


def without_description(records: Sequence[PromptRecord]) -> list[PromptRecord]:
    """The records whose description is empty."""
    return [record for record in records if not record.description]
