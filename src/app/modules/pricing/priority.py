"""Rule precedence (Functional Spec §15, Data Spec §3).

Within a rule family the most specific audience is checked first (Customer, Banner, Marketing Flag / group, Channel,
general rule); explicit priority (lower value first: campaign priority, then rule priority) resolves equal
specificity. The first rule of a family that matches the line is that family's candidate. Families then compete by
Best Price unless a rule is EXCLUSIVE.
"""

from app.modules.pricing.eligibility import specificity_rank
from app.modules.pricing.model import MONETARY_FAMILIES, Campaign, Rule


def sort_key(campaign: Campaign, rule: Rule) -> tuple:
    return (specificity_rank(campaign, rule), campaign.priority, rule.priority, campaign.campaign_id, rule.rule_id)


def family_rules(campaigns: list[Campaign], family: str) -> list[tuple[Campaign, Rule]]:
    """The rules of one family, in the order they are checked."""
    pairs = [(c, r) for c in campaigns for r in c.rules if r.family == family]
    return sorted(pairs, key=lambda p: sort_key(*p))


def check_order(campaigns: list[Campaign]) -> list[tuple[Campaign, Rule]]:
    """Every monetary rule, family by family, in the order the engine checks them."""
    return [p for family in MONETARY_FAMILIES for p in family_rules(campaigns, family)]
