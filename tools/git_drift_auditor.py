#!/usr/bin/env python3
"""Git Drift Auditor — categorizes uncommitted changes across SAHIIX repos."""
import subprocess
import json
from pathlib import Path
from collections import defaultdict

REPOS = [
    "/home/sahiix/sahiix-agi",
    "/home/sahiix/agency-agents",
    "/home/sahiix/saas-agent-platform",
    "/home/sahiix/friday-os",
    "/home/sahiix/kimi-core",
]

CATEGORIES = {
    "code_fixes": [],
    "data_artifacts": [],
    "config_drift": [],
    "secrets_risk": [],
    "unknown": [],
}

SECRET_PATTERNS = {".env", "credentials", "token", "secret", "api_key", "password", "private_key"}
DATA_PATTERNS = {"node_modules", "__pycache__", ".venv", "dist", "build", "*.log", "data/", ".sqlite", ".db"}
CONFIG_PATTERNS = {"config/", ".yaml", ".yml", ".toml", ".ini", "nginx", "systemd"}


def classify(path_str: str) -> str:
    p = path_str.lower()
    if any(s in p for s in SECRET_PATTERNS):
        return "secrets_risk"
    if any(d in p for d in DATA_PATTERNS):
        return "data_artifacts"
    if any(c in p for c in CONFIG_PATTERNS):
        return "config_drift"
    if p.endswith((".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp", ".c", ".h")):
        return "code_fixes"
    return "unknown"


def audit_repo(repo_path: str):
    result = subprocess.run(
        ["git", "-C", repo_path, "status", "--short"],
        capture_output=True, text=True,
    )
    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    for line in lines:
        if not line.strip():
            continue
        status = line[:2].strip()
        path = line[3:].strip()
        cat = classify(path)
        CATEGORIES[cat].append({"repo": repo_path, "status": status, "path": path})


def main():
    for repo in REPOS:
        if Path(repo, ".git").exists():
            audit_repo(repo)

    summary = {k: len(v) for k, v in CATEGORIES.items()}
    total = sum(summary.values())

    print("=" * 60)
    print(f"GIT DRIFT AUDIT — {total} uncommitted items across {len(REPOS)} repos")
    print("=" * 60)
    for cat, items in CATEGORIES.items():
        if not items:
            continue
        print(f"\n🔹 {cat.upper().replace('_', ' ')} ({len(items)})")
        for item in items[:10]:
            print(f"   [{item['status']:2s}] {item['path']}")
        if len(items) > 10:
            print(f"   ... and {len(items)-10} more")

    # Actionable report
    print("\n" + "=" * 60)
    print("ACTIONS")
    print("=" * 60)
    if CATEGORIES["secrets_risk"]:
        print("🚨 BLOCK: Review secrets_risk items before any commit")
    if CATEGORIES["data_artifacts"]:
        print("🧹 CLEAN: Add data artifacts to .gitignore (node_modules, __pycache__, etc.)")
    if CATEGORIES["config_drift"]:
        print("⚙️  REVIEW: Config changes may need manual validation")
    if CATEGORIES["code_fixes"]:
        print("✅ COMMIT: Code fixes are safe to stage and commit")
    if CATEGORIES["unknown"]:
        print("❓ CHECK: Unknown items need manual classification")

    # Write JSON for automation
    report_path = "/tmp/git_drift_report.json"
    with open(report_path, "w") as f:
        json.dump({"summary": summary, "details": CATEGORIES}, f, indent=2)
    print(f"\n📄 Full report: {report_path}")


if __name__ == "__main__":
    main()
