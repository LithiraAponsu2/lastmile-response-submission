"""What each person is told: things other bodies did that concern their organisation, across every incident.

Built at read time from saved records the person may already open (participation, requests between bodies,
shared reports, public requests, AI suggestions). Nothing is sent by SMS or email, and an item never assigns work.
Whether an item is new is kept by the reader's browser, so shared demo roles do not mark each other's items read.
"""
from .team_accounts import ORGANISATIONS
from .team_asks import escalation
from .team_participation import lead_org
from .workspace import timestamp

SHORT = {"dmc": "DMC", "district": "District Secretariat", "division": "Divisional Secretariat", "ndrsc": "NDRSC", "water": "NWSDB", "rda": "RDA"}
# What another body did to a request; {it} is "your request" or "a request".
DID = {"recommend": "recommended {it}", "accept": "accepted {it}", "decline": "declined {it}", "start": "started work on {it}", "done": "marked {it} as delivered",
       "confirm": "confirmed delivery of {it}", "reopen": "reopened {it}: not done yet", "cancel": "cancelled {it}", "comment": "added an update on {it}"}
FUNDING = {"accept": "approved {it}", "decline": "returned {it}", "done": "marked {it} as released", "confirm": "confirmed receipt of {it}"}
LIMIT = 60


def name(org):
    return SHORT.get(org) or next((o["name"] for o in ORGANISATIONS if o["id"] == org), org)


def feed(views, others, actor):
    """`views` are TeamWorkspace.view() results of incidents the actor takes part in; `others` are in-scope incidents they do not."""
    org, items = actor["organisation"], []
    for doc in views:
        c = doc["coordination"]
        ref = {"incident_id": doc["id"], "incident_title": doc["title"]}
        def add(key, at, kind, title, detail="", tab=None, record=None, frm=None, pane="data"):
            if at:
                items.append({**ref, "id": f"{doc['id']}:{key}", "at": at, "kind": kind, "title": title, "detail": detail or "",
                              "tab": tab, "record": record, "pane": pane, "from": frm})

        # Being brought into an incident by another body.
        mine = (c.get("participation") or {}).get(org) or {}
        by = mine.get("by_org")
        if by and by != org:
            opened = mine.get("via") == "invited" and doc.get("created_at") and abs((timestamp(mine["at"]) - timestamp(doc["created_at"])).total_seconds()) < 60
            title = (f"{name(by)} opened an incident and added your organisation" if opened else
                     f"{name(by)} approved your organisation joining" if mine.get("via") == "joined" else
                     f"{name(by)} brought your organisation back in" if mine.get("via") == "returned" else f"{name(by)} added your organisation")
            add("participation", mine.get("at"), "incident", title, f"{doc['title']} · {c['district']}" + (f" · {mine['reason']}" if mine.get("reason") else ""), "overview", frm=by)

        if (c.get("participation_view") or {}).get("can_manage"):
            for r in c.get("join_requests", []):
                if r["organisation"] != org:
                    add(r["id"], r["at"], "join", f"{name(r['organisation'])} asks to join", r.get("note") or "Approve or decline from Organisations.", "overview", r["id"], r["organisation"])

        # Requests between bodies: every step another body takes on one that involves yours.
        for a in c.get("asks", []):
            up = escalation(c, a) or {}
            if org not in {a["from"], a["to"], up.get("to")}:
                continue
            what = a["title"] + (f" · {a['quantity']} {a['unit']}" if a.get("quantity") else "")
            for i, h in enumerate(a.get("history", [])):
                by = h.get("organisation")
                if by == org:
                    continue
                if h["step"] == "asked":
                    title = f"{name(by)} asked your organisation for help" if a["to"] == org else f"{name(by)} sent a request"
                elif h["step"] == "redirect" and a["to"] == org:
                    title = f"{name(by)} redirected a request to your organisation"
                elif h["step"] == "escalate":
                    title = f"{name(by)} escalated a request" + (" to you" if up.get("to") == org else "")
                else:
                    words = (a["kind"] == "funding" and FUNDING.get(h["step"])) or DID.get(h["step"], h["step"] + " {it}")
                    title = f"{name(by)} " + words.format(it="your request" if a["from"] == org else "a request")
                add(f"{a['id']}:{i}", h["at"], "ask", title, what + (f" · “{h['note']}”" if h.get("note") and h["step"] != "asked" else ""), "requests", a["id"], by)

        # Reports another body asked yours to acknowledge (the view keeps only those addressed to this organisation).
        for n in c.get("notifications", []):
            if n["from"] != org:
                title = next((s["title"] for s in doc["sources"] if s["id"] == n["source_id"]), "Shared report")
                add(n["id"], n["at"], "report", f"{name(n['from'])} shared a report with your organisation", f"{title} · {n['note']}" if n.get("note") else title, "sources", n["source_id"], n["from"])

        # Public requests reach the lead body, which files and checks them.
        if org == lead_org(c):
            for p in c.get("public_requests", []):
                reading = p.get("reading") or {}
                filed = next((h["at"] for h in p.get("history", []) if h.get("stage") == "filed"), None) or p.get("at")
                add(f"{p['id']}:in", filed, "public", f"Public request {p['code']} came in" + (" (urgent)" if reading.get("urgency") == "high" else ""),
                    reading.get("summary") or p["text"], "public", p["id"])
                if p.get("answer"):
                    add(f"{p['id']}:answer", p["answer"]["at"], "public", f"The resident answered on {p['code']}", p["answer"]["text"], "public", p["id"])

        # AI suggestions prepared for this person's organisation and role; a person still decides.
        for p in c.get("plans", []):
            if p.get("origin") == "event" and p.get("status") == "pending":
                key = (p.get("trigger") or {}).get("key", "")
                if key.startswith("public:"):
                    add(p["id"], p.get("created_at"), "suggestion", "Suggested request for a public request", p["title"], "public", key.removeprefix("public:"))
                else:
                    add(p["id"], p.get("created_at"), "suggestion", "AI suggestion to review", p["title"], "overview", "ai-suggestions")

        for o in c.get("offers", []):
            if o["recipient"] == org:
                add(o["id"], o.get("created_at"), "assignment", "Assignment proposed to your organisation", o.get("note") or f"{o['quantity']}", "requests", o["id"])

    # Incidents in this body's scope that it is not part of: it may be able to help.
    for doc in others:
        c = doc["coordination"]
        lead = lead_org(c)
        ref = {"incident_id": doc["id"], "incident_title": doc["title"], "tab": None, "record": None, "pane": "data"}
        if doc["status"] != "closed" and lead != org and doc.get("created_at"):
            items.append({**ref, "id": f"{doc['id']}:opened", "at": doc["created_at"], "kind": "incident", "title": f"New incident in {c['district']}, led by {name(lead)}",
                          "detail": f"{doc['title']} · your organisation is not taking part. Ask to join if you can help.", "from": lead})
        for r in c.get("join_requests", []):
            if r["organisation"] == org and r["status"] in {"declined"} and r.get("decided_at"):
                items.append({**ref, "id": f"{doc['id']}:{r['id']}", "at": r["decided_at"], "kind": "join", "title": f"{name(r.get('decided_org'))} declined your request to join",
                              "detail": r.get("reason") or doc["title"], "from": r.get("decided_org")})

    items.sort(key=lambda i: timestamp(i["at"]), reverse=True)
    return items[:LIMIT]
