"""One shared record of who holds what from the standing stock lists, across every incident.

Places & stock gives counted quantities. Incidents reserve from them through this ledger, which
lives in its own document and is changed only with version-checked (compare-and-swap) writes, so
two approvals can never both take the same 100 packs. Available = counted − held − accepted −
delivered since the count. A hold that nobody accepts expires after HOLD_HOURS; a declined or
cancelled request releases it; a confirmed delivery converts it.

Inside an incident a reservation is first a pending record (checked, not yet taken). It is taken
from the ledger only when a person applies the change, in the same request; if anything else in
that change fails, the hold is released again.
"""
from __future__ import annotations

import contextvars
import copy
import math
from datetime import datetime, timedelta, timezone

from .team_accounts import require
from .team_areas import DISTRICT_IDS, DIVISIONS
from .workspace import WorkspaceError, now_iso, timestamp, uid

LEDGER = "incident_00000000ab2c"
HOLD_HOURS = 2
FIELDS = {"reserve_stock": {"stock_id", "quantity", "ask_id", "note"}}
# How many units a need for N people takes. Stated on the card; a person can change the quantity.
RULES = {"food": ("dry-rations", lambda n: n, "one dry ration pack per person"),
         "water": ("water-bowser", lambda n: max(1, math.ceil(n * 15 / 5000)), "15 litres per person per day, about 5,000 litres per bowser"),
         "shelter": ("sleeping-mat", lambda n: n, "one sleeping mat per person")}
ACTIVE = {"held", "accepted"}
# Holds already taken for the change being saved in this request: the save does not check them twice.
TAKEN = contextvars.ContextVar("lastmile_stock_taken", default=None)


class Shortage(WorkspaceError):
    """Not enough left: the change must be re-planned, never retried blindly."""


def _centre(area):
    return (DIVISIONS.get(area) or {}).get("centre")


def _km(a, b):
    if not a or not b:
        return 9999
    r = math.pi / 180
    h = math.sin((b[0] - a[0]) * r / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


class Ledger:
    def __init__(self, store, reference):
        self.store, self.reference = store, reference

    def doc(self):
        try:
            return self.store.get(LEDGER)
        except WorkspaceError:
            return self.create()

    def create(self):
        """Made once at start-up, outside any incident save."""
        return self.store.ensure_internal({"id": LEDGER, "internal": True, "title": "Stock holds", "version": 1, "created_at": now_iso(),
                                           "updated_at": now_iso(), "status": "active", "holds": []})

    def lines(self):
        return {r["id"]: r for r in self.reference.dataset("stock")["records"] if not r.get("retired")}

    def _live(self, hold, now):
        if hold["state"] == "held" and timestamp(hold["expires_at"]) <= now:
            return False
        return hold["state"] in ACTIVE or hold["state"] == "delivered"

    def available(self, stock_id, holds=None, now=None):
        """Counted quantity minus everything held, accepted or delivered since the count."""
        line = self.lines().get(stock_id)
        if line is None:
            return None
        now = now or datetime.now(timezone.utc)
        counted_at = str(line.get("counted_at") or "")
        taken = 0
        for h in (holds if holds is not None else self.doc()["holds"]):
            if h["stock_id"] != stock_id or not self._live(h, now):
                continue
            if h["state"] == "delivered" and counted_at and (h.get("changed_at") or h["at"]) < counted_at:
                continue  # delivered before the stock was last counted: already out of the count
            taken += h["quantity"]
        return max(0, int(line.get("quantity") or 0) - taken)

    def held_for(self, stock_id):
        now = datetime.now(timezone.utc)
        return [h for h in self.doc()["holds"] if h["stock_id"] == stock_id and h["state"] in ACTIVE and self._live(h, now)]

    def take(self, reservations, actor):
        """Take pending reservations from the ledger in one compare-and-swap write; all or nothing."""
        made = []
        def apply(d):
            now = datetime.now(timezone.utc)
            for r in reservations:
                free = self.available(r["stock_id"], d["holds"], now)
                line = self.lines()[r["stock_id"]]
                if free is None or r["quantity"] > free:
                    raise Shortage(f"Only {free or 0} {line['item'].replace('-', ' ')} left at {line['location_name']}; another incident has taken the rest. Check again for alternatives.", 409)
                hold = {"id": uid("hold"), "stock_id": r["stock_id"], "quantity": r["quantity"], "incident_id": r["incident_id"], "reservation_id": r["id"],
                        "ask_id": r.get("ask_id"), "state": "held", "by": actor["id"], "at": now.isoformat(), "expires_at": (now + timedelta(hours=HOLD_HOURS)).isoformat()}
                d["holds"].append(hold)
                made.append(hold)
        self._change(apply)
        return made

    def set_state(self, hold_ids, state):
        if not hold_ids:
            return
        def apply(d):
            for h in d["holds"]:
                if h["id"] in hold_ids and h["state"] in ACTIVE:
                    h["state"], h["changed_at"] = state, now_iso()
        self._change(apply)

    def _change(self, fn):
        for attempt in range(5):
            doc = self.doc()
            try:
                return self.store.mutate(LEDGER, doc["version"], "stock-ledger", fn)
            except Shortage:
                raise
            except WorkspaceError as error:
                if error.status != 409 or attempt == 4:  # another save landed first: read the holds again
                    raise

    # ---- choosing stock for a need
    def suggest(self, need, people, area, owner_hint=None):
        """Lines that can cover the need for this many people: same district first, then nearest; split if one is not enough."""
        if need not in RULES or not people:
            return None
        item, rule, wording = RULES[need]
        wanted = rule(int(people))
        district = DIVISIONS[area]["district"] if area in DIVISIONS else DISTRICT_IDS[area]["name"] if area in DISTRICT_IDS else None
        here = _centre(area)
        candidates = []
        for line in self.lines().values():
            if line.get("item") != item or line.get("status") not in (None, "ready"):  # pledged stock is not here yet
                continue
            free = self.available(line["id"])
            if not free:
                continue
            same = (DIVISIONS.get(line.get("area")) or {}).get("district") == district
            candidates.append((not same, _km(here, _centre(line.get("area"))), line, free))
        candidates.sort(key=lambda c: (c[0], c[1]))
        picks, left = [], wanted
        for _, km, line, free in candidates[:12]:
            if left <= 0 or len(picks) == 3:
                break
            take = min(free, left)
            picks.append({"stock_id": line["id"], "quantity": take, "owner": line.get("owner"), "location": line["location_name"], "km": round(km) if km < 9999 else None, "free": free})
            left -= take
        return {"item": item, "wanted": wanted, "rule": wording, "picks": picks, "short": max(0, left)}


def apply_reservation(service, doc, actor, d):
    """A pending reservation inside the incident: checked against the ledger now, taken when the change is applied."""
    c = doc["coordination"]
    ledger = service.ledger
    line = ledger.lines().get(d.get("stock_id"))
    require(line is not None, "Choose a stock line from Places & stock.", 422)
    quantity = d.get("quantity")
    require(type(quantity) is int and 0 < quantity <= 1000000, "Reserve a whole number of units.", 422)
    if TAKEN.get() is None:  # previewing: check against the shared record now; saving: the holds are already taken
        pending = sum(r["quantity"] for r in c.get("reservations", []) if r["stock_id"] == line["id"] and r["state"] == "pending")
        free = ledger.available(line["id"])
        if quantity + pending > free:
            raise Shortage(f"Only {max(0, free - pending)} {line['item'].replace('-', ' ')} available at {line['location_name']} right now.", 409)
    if d.get("ask_id"):
        require(any(a["id"] == d["ask_id"] for a in c.get("asks", [])), "Linked request not found in this incident.", 404)
    c.setdefault("reservations", []).append({"id": uid("reserve"), "stock_id": line["id"], "item": line["item"], "location": line["location_name"],
        "owner": line.get("owner"), "quantity": quantity, "ask_id": d.get("ask_id"), "note": (d.get("note") or "")[:300], "state": "pending",
        "by": actor["id"], "at": now_iso(), "incident_id": doc["id"], "hold_id": None})
    return f"Reserve {quantity} {line['item'].replace('-', ' ')} from {line['location_name']}"


def prepare(service, doc, actor, change):
    """Preview a change on a copy; take whatever it reserves from the shared record. Returns the holds (may be none)."""
    preview = copy.deepcopy(doc)
    before = {r["id"] for r in preview["coordination"].get("reservations", [])}
    change(preview)
    pending = [r for r in preview["coordination"].get("reservations", []) if r["id"] not in before and r["state"] == "pending"]
    return service.ledger.take(pending, actor) if pending else []


def attach(doc, holds):
    """Inside the real save: link the new pending reservations, in order, to the holds taken for them."""
    pending = [r for r in doc["coordination"].get("reservations", []) if r["state"] == "pending" and not r["hold_id"]]
    for r, h in zip(pending, holds):
        r.update(state="held", hold_id=h["id"], expires_at=h["expires_at"])


def release(service, holds):
    service.ledger.set_state({h["id"] for h in holds}, "released")


def settle(service, changes):
    """After the save: the shared record follows the requests (accepted, released, delivered)."""
    for state, hold_ids in changes.items():
        service.ledger.set_state(set(filter(None, hold_ids)), state)


def follow_requests(service, doc):
    """A reservation follows its request: accepted holds it, declined or cancelled releases it, done delivers it."""
    c = doc["coordination"]
    asks = {a["id"]: a for a in c.get("asks", [])}
    changes = {"accepted": [], "released": [], "delivered": []}
    for r in c.get("reservations", []):
        ask = asks.get(r.get("ask_id"))
        if not ask or r["state"] not in {"held", "accepted"}:
            continue
        target = "delivered" if ask["status"] in {"done", "confirmed"} else "released" if ask["status"] in {"declined", "cancelled"} else "accepted" if ask["status"] in {"accepted", "in_progress"} else None
        if target and target != r["state"]:
            r["state"] = target
            changes[target].append(r["hold_id"])
    return changes
