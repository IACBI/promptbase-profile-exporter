from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# The kinds of PromptBase listing the exporter can read, and how each is
# written in filenames and headings. The kind is also the URL path:
# promptbase.com/prompt/<slug>, /bundle/<slug>, and /app/<slug>.
ITEM_TYPES = ("prompt", "bundle", "app")
ITEM_TYPE_PLURALS = {"prompt": "prompts", "bundle": "bundles", "app": "apps"}

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
    item_type: str = "prompt"

    @property
    def url(self) -> str:
        return f"https://promptbase.com/{self.item_type}/{self.slug}"

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


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ms_to_iso(milliseconds: int) -> str:
    """An epoch-milliseconds time as ISO 8601 UTC, or "" outside years 1 to 9999.

    Counted from the epoch rather than with ``fromtimestamp``, which depends on the
    platform's ``time_t`` (Windows refuses times before 1970) and overflows on a
    corrupt catalog's huge value; for real times the two give the same text.
    """
    try:
        return (_EPOCH + timedelta(milliseconds=milliseconds)).isoformat()
    except OverflowError:
        return ""


def ms_to_iso_or_none(milliseconds: int | None) -> str | None:
    """ISO 8601 UTC text for an epoch-milliseconds value, or None if unknown."""
    if milliseconds is None:
        return None
    return _ms_to_iso(milliseconds) or None  # out of range is as unknown as missing
