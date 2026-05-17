"""
context_probe — run this via the RunWhen MCP `run_script_and_wait` FIRST,
before committing any SLX that reaches a cluster.

The workspace `kubeconfig` secret on the runner frequently contains MULTIPLE
contexts and a current-context that points at the WRONG cluster (e.g. a
control-plane cluster with none of your workloads). This probe prints every
context and shows which one actually sees your target namespace, so you can
pin `env_vars={"KUBE_CONTEXT": "<right-one>"}` on commit_slx.

Run:
  run_script_and_wait(workspace_name=..., location=...,
      interpreter="python", run_type="task",
      secret_vars={"kubeconfig": "kubeconfig"},
      env_vars={"TARGET_NS": "<your-namespace>"}, script_path=<this file>)
"""
import os, subprocess


def main():
    kc = os.environ.get("kubeconfig") or os.environ.get("KUBECONFIG")
    ns = os.environ.get("TARGET_NS", "backend-services")
    print(f"kubeconfig -> {kc!r}; target namespace -> {ns}")
    if not kc or not os.path.isfile(kc):
        return [{
            "issue title": "kubeconfig secret not a readable file",
            "issue description": f"env kubeconfig={kc!r}",
            "issue severity": 1,
            "issue next steps": "Confirm the workspace maps a 'kubeconfig' "
                                "secret to env var 'kubeconfig'.",
        }]

    def run(*a, ctx=None, timeout=45):
        cmd = ["kubectl", "--kubeconfig", kc]
        if ctx:
            cmd += ["--context", ctx]
        cmd += list(a)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout)
            return r.returncode, (r.stdout or r.stderr).strip()
        except Exception as e:  # noqa: BLE001
            return -1, repr(e)

    rc, out = run("config", "view", "-o",
                  "jsonpath={range .contexts[*]}{.name}{'\\n'}{end}")
    contexts = [c for c in out.splitlines() if c.strip()]
    _, cur = run("config", "current-context")
    print(f"current-context: {cur}\ncontexts: {contexts}")
    for ctx in contexts:
        rc, out = run("get", "ns", ns, "-o", "name", ctx=ctx)
        verdict = "HAS target ns" if rc == 0 else f"no ({out[:120]})"
        print(f"  context={ctx} -> {verdict}")
    return []
