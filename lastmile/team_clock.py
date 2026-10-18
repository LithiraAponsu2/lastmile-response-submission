"""Business time for labelled historical exercises; account/audit time stays real."""
from datetime import datetime, timezone
from .workspace import timestamp


def business_now(coordination):
    return timestamp(coordination["replay_at"]) if coordination.get("replay_at") else datetime.now(timezone.utc)
