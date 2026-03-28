"""RunWhen Python SLI Template

SLI scripts return a health metric between 0.0 (unhealthy) and 1.0 (healthy).

Common patterns:
  - Ratio: healthy / total (e.g. 8/10 pods ready = 0.8)
  - Binary: 1.0 if check passes, 0.0 if it fails
  - Percentage: convert to 0-1 scale (e.g. 95% → 0.95)
"""

import os
import subprocess


def run_cmd(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main():
    namespace = os.environ.get("NAMESPACE", "default")
    context = os.environ.get("CONTEXT", "")
    kubeconfig = os.environ.get("kubeconfig", "")
    if kubeconfig:
        os.environ["KUBECONFIG"] = kubeconfig

    ctx_flag = f"--context={context}" if context else ""

    _, total_out, _ = run_cmd(
        f"kubectl get pods -n {namespace} {ctx_flag} --no-headers --request-timeout=30s"
    )
    _, ready_out, _ = run_cmd(
        f"kubectl get pods -n {namespace} {ctx_flag} --no-headers --request-timeout=30s "
        f"--field-selector=status.phase=Running"
    )

    total = len([l for l in total_out.splitlines() if l.strip()]) if total_out else 0
    ready = len([l for l in ready_out.splitlines() if l.strip()]) if ready_out else 0

    if total == 0:
        return 1.0

    metric = round(ready / total, 2)
    print(f"Pod health: {ready}/{total} ready (metric={metric})")
    return metric
