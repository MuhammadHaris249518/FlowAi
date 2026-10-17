"""Runs the whole demo locally, reading secrets from the repo's .env (values are never printed).

    python scripts/run_local.py talkse     # http://localhost:8000  (call page)  + /owner (owner console)
    python scripts/run_local.py api        # http://localhost:4000  (FlowCRM API, demo mode)
    python scripts/run_local.py web        # http://localhost:3000  (FlowCRM UI, demo mode)

Run each in its own terminal. FlowCRM needs `npm ci` in flowcrm/ once.
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env() -> dict:
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    env = {**os.environ, **load_env()}
    # Prisma doesn't need channel_binding; the direct URL is the pooled one without "-pooler"
    db_url = re.sub(r"[&?]channel_binding=[^&]*", "", env.get("DATABASE_URL", ""))
    flowcrm_db = {"DATABASE_URL": db_url, "DIRECT_URL": env.get("DIRECT_URL") or db_url.replace("-pooler.", ".")}

    if which == "talkse":
        python = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
        if not os.path.exists(python):
            python = sys.executable
        cmd, cwd = f'"{python}" -m uvicorn server:app --port 8000', "talkse"
        extra = {"FLOWCRM_URL": env.get("FLOWCRM_URL", "http://localhost:3000")}
    elif which == "api":
        cmd, cwd = "npx ts-node-dev --transpile-only src/server.ts", os.path.join("flowcrm", "apps", "api")
        extra = {**flowcrm_db, "PORT": "4000", "AUTH_MODE": "demo",
                 "DEMO_CLERK_ID": env.get("DEMO_CLERK_ID", "demo_haris"),
                 "DEMO_ORG_ID": env.get("DEMO_ORG_ID", env.get("CRM_ORG_ID", "org_coolbreeze")),
                 # AI Assistant: Groq LLM + the Talkse voice agent's tools over MCP
                 "TALKSE_MCP_URL": env.get("TALKSE_MCP_URL", "http://localhost:8000/mcp/")}
    elif which == "web":
        cmd, cwd = "npx next dev -p 3000", os.path.join("flowcrm", "apps", "web")
        extra = {"NEXT_PUBLIC_AUTH_MODE": "demo", "NEXT_PUBLIC_API_URL": "http://localhost:4000/api/v1",
                 "NEXT_PUBLIC_TALKSE_URL": "http://localhost:8000"}
    else:
        print(__doc__)
        sys.exit(1)

    subprocess.run(cmd, shell=True, cwd=os.path.join(ROOT, cwd), env={**env, **extra})


if __name__ == "__main__":
    main()
