"""Rehearsal-only routes. Tomorrow Siva's POST /approvals/{id} replaces /dev/approve."""
import json

from fastapi import APIRouter, HTTPException

from backend.agent import clock
from backend.agent.api import act_on
from backend.agent.deps import db, pact
from backend.agent.propose import USER_ID

router = APIRouter(prefix="/dev")


@router.post("/approve/{approval_id}")
def approve(approval_id: int):
    rows = db.query("SELECT * FROM approvals WHERE id = %s", (approval_id,))
    if not rows:
        raise HTTPException(404, "no such approval")
    # Only a pending approval can be approved; an executed one stays executed (A1 then stops it).
    db.execute("UPDATE approvals SET status = 'approved', decided_at = %s WHERE id = %s "
               "AND status = 'pending'", (clock.ts(), approval_id))
    approval = db.query("SELECT * FROM approvals WHERE id = %s", (approval_id,))[0]
    p = json.loads(db.query("SELECT payload_json FROM proposals WHERE id = %s",
                            (approval["proposal_id"],))[0]["payload_json"])
    the_pact, _ = pact.active_pact(USER_ID)
    ctx = pact.build_context(p["opportunity_id"], {"id": USER_ID})
    d = pact.run_approved(p, the_pact, ctx, approval)
    out = act_on(p, the_pact, d, source="approval")
    if out.get("status") == "done":
        db.execute("UPDATE approvals SET status = 'executed' WHERE id = %s", (approval_id,))
    return out
