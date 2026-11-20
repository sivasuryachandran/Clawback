# Note: `sign` is a module (backend/pact/sign.py); use `from backend.pact.sign import sign`.
from .sign import verify_text, proposal_hash
from .gate import GateContext, Decision, run_gate, run_approved
from .once import execute_once, AlreadyRan
from .context import active_pact, build_context, record      # the 3.4 contract, re-exported for B
from ..persona.webhook import verify_persona_signature

__all__ = ["verify_text", "proposal_hash", "GateContext", "Decision", "run_gate",
           "run_approved", "active_pact", "build_context", "record", "execute_once", "AlreadyRan", "verify_persona_signature"]
