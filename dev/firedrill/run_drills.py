"""Run Clawback's 6 Firedrill drills end to end on THIS machine (macOS or Linux).

    npm install -g firedrill-run && firedrill init        # once: browser sign-in, pick the workspace
    pip install -r requirements.txt                         # fastapi, uvicorn, pydantic, httpx, python-dotenv
    python -m dev.firedrill.run_drills                      # in the repo root

Uses the agent and world already in Sharan's workspace (ids below). Sign in to THAT workspace.
In another workspace pass --new: it rebuilds the library there (build_library) and registers a
new agent (that workspace needs its own credits, e.g. the event code).

Steps: make_local (machine-specific descriptor + plan) -> register a descriptor revision ->
world save on it -> plan review (free) -> YOU confirm -> apply (reserves credits) -> runner enroll
-> run (Firedrill asks you to approve the local plan) -> report quality.
--until-plan stops after the free plan review (safe to test anywhere, even on Windows).
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
AGENT_ID = "0c39de7d-f5e7-409e-a91f-ca4bee9becb1"
WORLD_ID = "0b87123b-0b11-456a-bf36-d92e6d414bcd"


def cli() -> list[str]:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
    js = Path(root) / "firedrill-run" / "bin" / "firedrill.mjs"
    return [shutil.which("node") or "node", str(js)] if js.exists() else ["firedrill"]


def fd_json(*args: str) -> list[dict]:
    r = subprocess.run([*cli(), *args, "--json"], capture_output=True, text=True, timeout=900)
    events = [json.loads(l) for l in r.stdout.splitlines() if l.strip().startswith("{")]
    if r.returncode != 0:
        raise SystemExit(f"firedrill {' '.join(args)} failed ({r.returncode}):\n{(r.stdout + r.stderr)[-1500:]}")
    return events


def find(obj, *keys):
    """First value for any of `keys` anywhere in nested JSON."""
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k):
                return obj[k]
        for v in obj.values():
            r = find(v, *keys)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find(v, *keys)
            if r:
                return r
    return None


def main() -> int:
    until_plan = "--until-plan" in sys.argv
    new = "--new" in sys.argv
    if sys.platform == "win32" and not until_plan:
        raise SystemExit("Firedrill runs drills on macOS or Linux only (docs). On Windows use --until-plan.")
    py = sys.executable

    agent, world = AGENT_ID, WORLD_ID
    if new:
        subprocess.run([py, "-m", "dev.firedrill.build_library"], check=True)
    subprocess.run([py, "-m", "dev.firedrill.make_local"], check=True)

    # 1. descriptor revision for THIS machine's folder identity
    if new:
        ev = fd_json("register", "--descriptor", str(OUT / "descriptor.json"), "--name", "Clawback (Teammate B)")
    else:
        hist = fd_json("descriptors", "--agent", agent)
        latest = max((d.get("revision", 0) for e in hist for d in (find(e, "results") or [])), default=0)
        ev = fd_json("register", "--descriptor", str(OUT / "descriptor.json"), "--agent", agent,
                     "--expected-revision", str(latest))
    agent = find(ev, "agent_id") or agent
    desc_rev_id = find(ev[-1], "id")
    digest = find(ev, "descriptor_sha256")
    print(f"descriptor registered: agent {agent}, digest {digest[:12]}...")
    subprocess.run([py, "-m", "dev.firedrill.make_local", "--digest", digest], check=True)

    # 2. world bound to that descriptor revision
    if new:
        ev = fd_json("world", "save", "--agent", agent, "--file", str(HERE / "world.json"))
    else:
        hist = fd_json("world", "history", world)
        latest = max((d.get("revision", 0) for e in hist for d in (find(e, "results") or [])), default=1)
        ev = fd_json("world", "save", "--agent", agent, "--file", str(HERE / "world.json"),
                     "--world", world, "--expected-revision", str(latest), "--descriptor-revision", desc_rev_id)
    world = find(ev, "world_id") or world
    print(f"world saved: {world}")

    # 3. plan review (free)
    ev = fd_json("plan", "--world", world)
    plan = ev[-1]["data"]
    rv = plan.get("review", {})
    print(f"\nPLAN {plan['id']}: {rv.get('requested_drills')} drills, gaps={rv.get('gaps')}, "
          f"spends_credits={rv.get('spends_credits')}, expires {plan.get('expires_at')}")
    if rv.get("gaps"):
        raise SystemExit("The plan has gaps; fix them before applying.")
    if until_plan:
        print("--until-plan: stopping before apply. Nothing reserved or spent.")
        return 0
    if input("\nApply (reserves credits for these drills)? Type yes: ").strip().lower() != "yes":
        print("Not applied. Nothing spent.")
        return 1

    # 4. apply -> campaign (interactive output so the reserved amount is visible)
    r = subprocess.run([*cli(), "apply", plan["id"], "--approve", "--follow", "--json"], capture_output=True, text=True, timeout=1800)
    print(r.stdout[-2000:])
    events = [json.loads(l) for l in r.stdout.splitlines() if l.strip().startswith("{")]
    campaign = find(events, "campaign_id")
    if r.returncode != 0 or not campaign:
        raise SystemExit(f"apply did not return a campaign id:\n{(r.stdout + r.stderr)[-1500:]}")
    print(f"campaign {campaign}")

    # 5. runner + run (Firedrill prints the local plan and asks YOU to approve it)
    subprocess.run([*cli(), "runner", "enroll"], check=True)
    subprocess.run([*cli(), "run", "--plan", str(OUT / "plan.json"), "--descriptor", str(OUT / "descriptor.json"),
                    "--campaign", campaign], check=False)

    # 6. report: only Firedrill's own numbers
    subprocess.run([*cli(), "report", campaign], check=False)
    subprocess.run([*cli(), "report", campaign, "quality"], check=False)
    print(f"\nCampaign {campaign}. Quote only what `firedrill report {campaign} quality` shows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
