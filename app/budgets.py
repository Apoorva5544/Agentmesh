from dataclasses import dataclass

from app.db import Database
from app.db import as_record_dict


@dataclass
class BudgetVerdict:
    status: str  # "ok" | "warn" | "blocked" | "unconfigured"
    spend_inr: float = 0.0
    budget_inr: float = 0.0
    ratio: float = 0.0
    fallback_model: str | None = None


class CostGuard:
    """Pushes spend against a per-agent monthly budget, fires alerts, and
    (optionally) hard-blocks once the limit is reached."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def evaluate(self, agent_id: str, projected_cost_inr: float = 0.0) -> BudgetVerdict:
        row = await self.db.get_agent(agent_id)
        if row is None:
            return BudgetVerdict(status="unconfigured")
        agent = as_record_dict(row)
        budget = float(agent.get("monthly_budget_inr") or 0.0)
        if budget <= 0:
            return BudgetVerdict(status="unconfigured")

        spend = await self.db.monthly_spend_inr(agent_id)
        ratio = round(spend / budget, 4) if budget else 0.0
        thresholds = [float(t) for t in (agent.get("alert_thresholds") or [0.5, 0.8, 0.95])]
        hard_limit = bool(agent.get("hard_limit"))
        fallback_model = agent.get("fallback_model")

        for threshold in sorted(thresholds):
            if ratio >= threshold and not await self.db.alert_fired_this_month(agent_id, threshold):
                await self.db.record_alert(agent_id, threshold, spend, budget)

        if hard_limit and spend + projected_cost_inr >= budget:
            return BudgetVerdict(
                status="blocked",
                spend_inr=round(spend, 2),
                budget_inr=budget,
                ratio=ratio,
                fallback_model=fallback_model,
            )
        warn_threshold = sorted(thresholds)[-2] if len(thresholds) >= 2 else 0.8
        if ratio >= warn_threshold:
            return BudgetVerdict(
                status="warn",
                spend_inr=round(spend, 2),
                budget_inr=budget,
                ratio=ratio,
                fallback_model=fallback_model,
            )
        return BudgetVerdict(
            status="ok",
            spend_inr=round(spend, 2),
            budget_inr=budget,
            ratio=ratio,
            fallback_model=fallback_model,
        )