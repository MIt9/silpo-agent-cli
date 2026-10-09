"""Loyalty: read-only `loyalty` subcommand. One balance/rewards snapshot
from the four promo/loyalty tools no command previously surfaced together.

Real schema (docs/mcp_schema.md, live-verified):
- `silpo_get_loyalty_info()` -> `{"loyalty": {"card": {...}, "balance":
  {"total", "accounts": [...]}}}`; `balance.total` mirrors the cart's
  `loyalty.bonusAvailable`.
- `silpo_get_my_promos()` -> `{"promos": [{"promoId", "selected",
  "description", ...}], "meta"}`; no tool writes a promo selection.
- `silpo_get_promo_codes()` -> `{"promoCodes": [...], "meta"}`.
- `silpo_get_my_certificates({"limit", "offset"})` -> `{"certificates":
  [{"barcode", "totalPrice", "expireDate", "title", ...}]}`.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoyaltySummary:
    balance: float | None = None
    card_name: str | None = None
    promos_total: int = 0
    promos_selected: int = 0
    promo_codes: tuple = ()
    certificates: tuple = field(default_factory=tuple)

    def format(self) -> list[str]:
        lines = [f"Bonus balance: {self.balance:.2f}" if isinstance(self.balance, (int, float)) else "Bonus balance: ?"]
        if self.card_name:
            lines.append(f"Card: {self.card_name}")
        lines.append(f"Promos: {self.promos_selected}/{self.promos_total} selected")
        if self.promo_codes:
            lines.append(f"Promo codes ({len(self.promo_codes)}):")
            lines.extend(f"  - {code}" for code in self.promo_codes)
        else:
            lines.append("Promo codes: none")
        if self.certificates:
            lines.append(f"Certificates ({len(self.certificates)}):")
            lines.extend(f"  - {cert}" for cert in self.certificates)
        else:
            lines.append("Certificates: none")
        return lines


def get_loyalty_summary(client) -> LoyaltySummary:
    loyalty_response = client.call("silpo_get_loyalty_info") or {}
    loyalty = loyalty_response.get("loyalty") or {}
    balance = (loyalty.get("balance") or {}).get("total")
    card = loyalty.get("card") or {}
    card_name = card.get("typeName")

    promos_response = client.call("silpo_get_my_promos") or {}
    promos = promos_response.get("promos") or []
    selected = sum(1 for p in promos if p.get("selected"))

    codes_response = client.call("silpo_get_promo_codes") or {}
    codes = codes_response.get("promoCodes") or []

    certs_response = client.call("silpo_get_my_certificates", {"limit": 10, "offset": 0}) or {}
    certs = []
    for cert in certs_response.get("certificates") or []:
        title = cert.get("title") or cert.get("barcode") or "?"
        expire = cert.get("expireDate")
        total = cert.get("totalPrice")
        detail = title
        if isinstance(total, (int, float)):
            detail += f" ({total:.2f})"
        if expire:
            detail += f" until {expire}"
        certs.append(detail)

    code_labels = []
    for code in codes:
        if isinstance(code, dict):
            code_labels.append(code.get("code") or code.get("promoCode") or str(code.get("id") or code))
        else:
            code_labels.append(str(code))

    return LoyaltySummary(
        balance=balance,
        card_name=card_name,
        promos_total=len(promos),
        promos_selected=selected,
        promo_codes=tuple(code_labels),
        certificates=tuple(certs),
    )
