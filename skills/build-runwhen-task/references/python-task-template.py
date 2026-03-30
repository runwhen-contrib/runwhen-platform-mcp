"""RunWhen Python Task Template

Contract-compliant python task script. Copy and adapt for your health check.

Contract requirements:
  - Define top-level main() function (runner invokes it)
  - Return List[Dict] with keys: 'issue title', 'issue description',
    'issue severity' (int 1-4), 'issue next steps'
  - Optionally include 'issue observed at' (ISO timestamp)
  - Never call main() directly
  - Never use if __name__ == "__main__"

Secrets are injected as file paths via env vars.
Example: os.environ["kubeconfig"] is a FILE PATH, not the value.
"""

import os
import subprocess


def read_secret(env_var: str) -> str:
    """Read a secret that may be a file path (runner) or direct value (local)."""
    val = os.environ.get(env_var, "")
    if val and os.path.isfile(val):
        with open(val) as f:
            return f.read().strip()
    return val.strip()


def run_cmd(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main():
    issues = []

    namespace = os.environ.get("NAMESPACE", "default")
    context = os.environ.get("CONTEXT", "")
    kubeconfig = os.environ.get("kubeconfig", "")
    if kubeconfig:
        os.environ["KUBECONFIG"] = kubeconfig

    ctx_flag = f"--context={context}" if context else ""

    # Example: check for pods not in Running state
    rc, stdout, stderr = run_cmd(
        f"kubectl get pods -n {namespace} {ctx_flag} "
        f"--no-headers --request-timeout=30s "
        f"--field-selector=status.phase!=Running,status.phase!=Succeeded"
    )

    if stdout:
        lines = [line for line in stdout.splitlines() if line.strip()]
        pod_names = [line.split()[0] for line in lines[:5]]

        issues.append(
            {
                "issue title": f"{len(lines)} pod(s) not ready in namespace {namespace}",
                "issue description": f"Pods not in Running/Succeeded state: {', '.join(pod_names)}",
                "issue severity": 2,
                "issue next steps": (
                    f"Run: kubectl describe pod <name> -n {namespace} "
                    "to check events and conditions"
                ),
            }
        )

    # Print diagnostic info (appears in report)
    print(f"Checked namespace: {namespace}")
    print(f"Issues found: {len(issues)}")

    return issues
