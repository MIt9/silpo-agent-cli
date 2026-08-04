"""Coupons Lister: read-only `coupons` subcommand (issue #36, part of #34).
Lists the user's active loyalty coupons with their buying condition,
validity dates, and reward. Informational only -- makes no mutating MCP
calls.

Real schema (live-verified, see ../../docs/mcp_schema.md's "Promo / loyalty
tools" section):
- `silpo_get_my_coupons()` -> `{"success", "summary", "coupons": [{"id",
  "active", "useWay", "beginDate", "endDate", "description", "limitText",
  "warningText", "image"}]}`.
- `silpo_get_coupon_details({"businessCouponId": id})` -> `{"success",
  "coupon": {"id", "active", "state", "useWay", "beginDate", "endDate",
  "usedCount", "description", "limitText", "warningText", "rewardText",
  "rewardValue", "image"}}`, keyed off `coupons[].id`.

Scope decision: only active coupons get a details lookup -- an inactive
coupon's reward isn't actionable, same scope-narrowing style as
promo_optimizer.py (issue #20).

Explicitly does NOT attempt to match a coupon's free-text `description`
(the buying condition, e.g. "за купівлю вершкового масла") against cart
contents, search results, or promo alternatives. No coupon exposes a linked
product id, only descriptive text -- this exact heuristic was already
rejected for promo alternatives (see docs/mcp_schema.md's "Promo / loyalty
tools" section and promo_optimizer.py's module docstring).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CouponLine:
    description: str
    begin_date: str | None
    end_date: str | None
    reward_text: str | None
    reward_value: float | None

    def format(self) -> str:
        dates = f"{self.begin_date or '?'} - {self.end_date or '?'}"
        reward = self.reward_text or (None if self.reward_value is None else str(self.reward_value))
        line = f"{self.description} ({dates})"
        if reward:
            line += f" -- reward: {reward}"
        return line


def list_coupons(client) -> list[CouponLine]:
    response = client.call("silpo_get_my_coupons") or {}
    coupons = response.get("coupons") or []
    active = [c for c in coupons if c.get("active")]

    lines = []
    for coupon in active:
        details_response = client.call("silpo_get_coupon_details", {"businessCouponId": coupon["id"]}) or {}
        detail = details_response.get("coupon") or {}
        lines.append(
            CouponLine(
                description=coupon.get("description") or "",
                begin_date=coupon.get("beginDate"),
                end_date=coupon.get("endDate"),
                reward_text=detail.get("rewardText"),
                reward_value=detail.get("rewardValue"),
            )
        )
    return lines
