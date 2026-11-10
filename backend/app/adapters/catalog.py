"""Demo product catalog, loaded from app/data/catalog.json.

Item names and descriptions are seller-written text: untrusted. The agent may
read them, and one listing deliberately contains a prompt injection. Nothing
in this module or the policy engine ever interprets that text.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.domain.money import Money

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.json"


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: str
    name: str
    description: str  # untrusted
    price: Money
    category: str
    merchant_id: str  # payee email
    merchant_name: str


class Catalog:
    def __init__(self, items: list[CatalogItem]):
        self._items = {item.id: item for item in items}

    @classmethod
    def load(cls, merchant_email: str, path: Path = _DEFAULT_PATH) -> "Catalog":
        raw = json.loads(path.read_text(encoding="utf-8"))
        merchants = {
            key: (m["name"], m["email"].replace("${DEMO_MERCHANT_EMAIL}", merchant_email))
            for key, m in raw["merchants"].items()
        }
        items = []
        for entry in raw["items"]:
            name, email = merchants[entry["merchant"]]
            items.append(
                CatalogItem(
                    id=entry["id"],
                    name=entry["name"],
                    description=entry["description"],
                    price=Money.of(entry["price"], entry["currency"]),
                    category=entry["category"],
                    merchant_id=email,
                    merchant_name=name,
                )
            )
        return cls(items)

    def get(self, item_id: str) -> CatalogItem | None:
        return self._items.get(item_id)

    def search(self, query: str = "", limit: int = 10) -> list[CatalogItem]:
        """Every query word must appear in the item's name, description or category."""
        words = query.casefold().split()

        def matches(item: CatalogItem) -> bool:
            text = f"{item.name} {item.description} {item.category}".casefold()
            return all(word in text for word in words)

        return [item for item in self._items.values() if matches(item)][:limit]
