"""Cross-incident exercise pools under the application's ONE-writer contract.

The incident store holds its re-entrant lock throughout the action transaction.
Every read/check/commit is serialized across this writer. This is not a claim of
multi-process serializability: the existing service must not run multiple active
writers. Explicit owner-chosen keys link records; no AI guesses physical identity.
"""
from .team_accounts import require
from .workspace import timestamp


def resource_snapshot(store, coordination, asset):
    key = asset.get("resource_key")
    if not key:
        return asset["facts"], [], False
    facts = dict(asset["facts"])
    offers = []
    for doc in store.team_documents():
        other = doc["coordination"]
        if doc["id"] == coordination.get("incident_id"):
            continue
        matches = [a for a in other["assets"] if a.get("resource_key") == key and a["owner"] == asset["owner"]]
        for candidate in matches:
            require(candidate["kind"] == asset["kind"], "Shared resource identity has conflicting kinds.")
            require(other.get("replay_at") == coordination.get("replay_at"), "Linked resource incidents must share the same replay clock mode/time.")
            for field, value in candidate["facts"].items():
                current = facts.get(field)
                if current and timestamp(value["observed_at"]) == timestamp(current["observed_at"]):
                    require(value["value"] == current["value"], "Shared resource has conflicting equally timed owner snapshots; request a corrected owner update.")
                if not current or timestamp(value["observed_at"]) > timestamp(current["observed_at"]):
                    facts[field] = value
            offers.extend(o for o in other["offers"] if o.get("asset_id") == candidate["id"])
    return facts, offers, True


def pool_approved(store, coordination, scheme):
    records = [f for f in coordination["finance"] if f["scheme_id"] == scheme["id"]]
    pool = scheme.get("pool_key")
    if pool:
        for doc in store.team_documents():
            if doc["id"] == coordination.get("incident_id"):
                continue
            other = doc["coordination"]
            matches = [s for s in other["schemes"] if s.get("pool_key") == pool and s["owner"] == scheme["owner"]]
            for candidate in matches:
                require(all(candidate[k] == scheme[k] for k in ("budget", "limit", "currency")), "Linked funding pool policies conflict; approval is blocked.")
                records.extend(f for f in other["finance"] if f["scheme_id"] == candidate["id"])
    from decimal import Decimal
    return sum((Decimal(f["approved"] or "0") for f in records if f["status"] != "cancelled"), Decimal("0"))
