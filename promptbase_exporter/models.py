from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Optional record fields, written only when requested (--extra-fields). The
# order here is the order of the columns in the output.
EXTRA_FIELDS = (
    "tags",
    "engine",
    "nsfw",
    "featured",
    "updated",
    "last_sale",
    "unique_sales",
)


@dataclass(frozen=True)
class Profile:
    username: str
    uid: str


@dataclass(frozen=True)
class PromptRecord:
    title: str
    description: str
    slug: str
    prompt_type: str
    domain: str
    created: int
    price: float
    discount: float = 0.0
    views: int = 0
    sales: int = 0
    downloads: int = 0
    favorites: int = 0
    rating: float = 0.0
    reviews: int = 0
    # Extra fields. None means PromptBase does not record the value for this
    # prompt (as opposed to a recorded zero).
    tags: tuple[str, ...] = ()
    engine: str = ""
    nsfw: bool = False
    featured: bool = False
    updated: int | None = None
    last_sale: int | None = None
    unique_sales: int = 0

    @property
    def url(self) -> str:
        return f"https://promptbase.com/prompt/{self.slug}"

    @property
    def created_iso(self) -> str:
        if not self.created:
            return ""
        return _ms_to_iso(self.created)

    @property
    def is_text(self) -> bool:
        return self.domain == "text"

    @property
    def is_image(self) -> bool:
        return self.domain == "image"

    @property
    def is_free(self) -> bool:
        return self.price == 0


def _ms_to_iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def ms_to_iso_or_none(milliseconds: int | None) -> str | None:
    """ISO 8601 UTC text for an epoch-milliseconds value, or None if unknown."""
    return None if milliseconds is None else _ms_to_iso(milliseconds)
