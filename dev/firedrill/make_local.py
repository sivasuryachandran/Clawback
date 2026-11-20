"""Write the MACHINE-SPECIFIC Firedrill files (descriptor.json, plan.json) for this checkout.
Run it on the machine that runs drills (macOS/Linux): the root identity hashes this folder's
real path + device/inode, so it differs per machine and per clone.

    python -m dev.firedrill.make_local                       # writes dev/firedrill/out/{descriptor,plan}.json
    firedrill register --descriptor dev/firedrill/out/descriptor.json --name "Clawback (B)"
      (or, for an existing agent: --agent <id> --expected-revision <n>)
    python -m dev.firedrill.make_local --digest <digest printed by register>   # fills plan.json

plan.json stays local (Firedrill's docs: keep it out of version control); out/ is git-ignored.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = HERE / "out"

# Firedrill's documented one-liner (connect/register-agent.md), run with node so the hash matches.
IDENTITY_JS = ('const c=require("crypto"),f=require("fs");const p=f.realpathSync(process.argv[1]);'
               'const s=f.statSync(p,{bigint:true});console.log(c.createHash("sha256")'
               '.update("firedrill.root-identity.v1\\0").update(p).update("\\0").update(`${s.dev}:${s.ino}`).digest("hex"))')


def root_identity(path: Path) -> str:
    r = subprocess.run(["node", "-e", IDENTITY_JS, str(path)], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def main() -> int:
    digest = sys.argv[sys.argv.index("--digest") + 1] if "--digest" in sys.argv else "<run firedrill register, then --digest>"
    OUT.mkdir(exist_ok=True)
    ident = root_identity(REPO)
    adapter_sha = hashlib.sha256((HERE / "adapter.py").read_bytes()).hexdigest()
    descriptor = {
        "schema": "firedrill.local-descriptor.v1",
        "source_roots": [{"alias": "app", "identity_sha256": ident}],
        "services": [{"alias": "agent", "root_alias": "app", "role": "agent", "depends_on": [], "configuration": []}],
        "entry_service": "agent",
        "driver": {"profile": "http.v1", "adapter_sha256": adapter_sha, "authentication": "anonymous",
                   "state_domains": ["proc"]},
        "state_domains": [{"alias": "proc", "service_alias": "agent", "kind": "process", "location": "local",
                           "reset": "fresh-process", "concurrency": "independent", "hook_sha256": None}],
        "dependencies": [],
    }
    plan = {
        "version": 1,
        "descriptor_sha256": digest,
        "roots": [{"alias": "app", "path": str(REPO), "identity_sha256": ident}],
        "services": [{
            "alias": "agent", "root_alias": "app", "mode": "launch",
            "command": {"executable": sys.executable, "args": ["-m", "dev.firedrill.adapter"]},
            "cwd": ".", "env_passthrough": [], "env_static": {"CLAWBACK_USE_STUBS": "1", "DEMO_MODE": "true"},
            "local_secret_env": [],
            "readiness": {"kind": "http", "url": "http://127.0.0.1:0/health", "expect_status": [200]},
            "readiness_timeout_ms": 60000, "stop_timeout_ms": 5000, "per_drill": True, "port_env": "PORT",
        }],
        "driver": {"profile": "http.v1", "service_alias": "agent", "path": "/chat", "method": "POST",
                   "stream": "none", "session_header": None, "request_template": "message-text",
                   "response_pointer": "/reply"},
        "state_hooks": [], "max_parallel_drills": 1,
    }
    (OUT / "descriptor.json").write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    (OUT / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"root identity {ident[:12]}..., adapter {adapter_sha[:12]}...\nwrote {OUT / 'descriptor.json'} and {OUT / 'plan.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
