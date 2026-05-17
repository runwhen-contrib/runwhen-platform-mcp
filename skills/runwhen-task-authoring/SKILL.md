---
name: runwhen-task-authoring
description: "Author, test, and commit custom Python SLX tasks that reach platform internals (Postgres, Redis, Neo4j, TaskIQ/Celery, pod logs) via kubectl port-forward + workspace-secret credentials. Use when: (1) Building a RunWhen triage/diagnostic SLX that must query a cluster's databases or logs, (2) An SLX needs port-forward + a real driver rather than the public API, (3) Using commit_slx / run_script_and_wait / run_slx for infra-reaching scripts, (4) Hitting their non-obvious gotchas (kubecontext selection, script_path vs base64, sslmode, secret encoding, run_slx polling), or (5) Extending the build-runwhen-task workflow with infra-access patterns."
---

# RunWhen Task Authoring (infra-reaching SLXs)

Hard-won rules for writing custom SLX scripts that pull data from a RunWhen
platform deployment's internals. Each rule here cost a failed remote run to
learn. Read the **Hard rules** before writing code. This skill layers
infra-access patterns on top of `build-runwhen-task` (use that for the
general SLX lifecycle and the CodeBundle Registry check first).

`references/` in this skill (use them directly):
- `slx_preamble.py` — the verified, byte-stable shared preamble
  (port-forward, secret resolution, kubeconfig/context handling, `pg()`,
  a `resolve_*`-style helper, the issue contract). Embed verbatim; only
  `main()` differs per task, and keep it identical across a task suite.
- `context_probe.py` — run via `run_script_and_wait` FIRST to discover
  which kubeconfig context actually sees your namespace.
- `gen_env.sh` — generate local creds (`.env.local`, shell-sourced) AND a
  RAW `.values` file for pasting into the workspace-secret UI. (Worked
  example; adapt the cluster context + secret names to your deployment.)
- `run_local.sh` — run an SLX's `main()` locally via importlib (no
  `if __name__`), printing the contract issues + stdout.

## Hard rules (each was a real failure)

1. **Language = Python.** Contract: define `def main()` returning
   `list[dict]`, each with keys `"issue title"`, `"issue description"`,
   `"issue severity"` (int 1=urgent…4=low), `"issue next steps"`, optional
   `"issue observed at"`. **No `if __name__ == "__main__"`** — the runner
   imports the module and calls `main()`.
2. **Always print, optionally `add_issue`.** Make the shared `add_issue()`
   helper *also* `print()` a formatted block. Task reports surface stdout;
   structured issues alone can be lost. Benign/informational findings →
   plain `print()` only.
3. **Access via `kubectl port-forward`, NOT `kubectl exec`.** Exec is often
   RBAC-denied on the runner's kubeconfig. Port-forward the in-cluster
   Service and connect with a real Python driver. `kubectl logs` is fine
   (logs RBAC ≠ exec RBAC).
4. **Credentials come from workspace secrets**, injected as **file paths** —
   read with `open(path).read().strip()`. Do not `kubectl get secret` from
   the SLX (also needs RBAC) and never hard-code. `secret_vars` maps
   env-var name → workspace secret key.
5. **kubecontext is never hard-coded.** The runner `kubeconfig` secret
   commonly has multiple contexts and a current-context pointing at the
   WRONG cluster. Run `references/context_probe.py` via
   `run_script_and_wait` first; then pin
   `env_vars={"KUBE_CONTEXT": "<the-right-one>"}` on `commit_slx`. The
   preamble uses current-context only when `KUBE_CONTEXT` is empty.
6. **Crunchy pgBouncer mandates TLS** → connect with `sslmode=require`
   (`sslmode=prefer` "works" but emits a misleading non-SSL fallback
   error). Use pgBouncer-safe psycopg: `prepare_threshold=None`,
   `autocommit=True`. Postgres often has **per-database users** (one cred
   set per DB).
7. **commit_slx / run_script_*: use `script_path` (absolute local path),
   NOT inline `script_base64`.** The MCP server runs locally and reads the
   file byte-perfectly; pasting ~20 KB of base64 inline gets truncated /
   charset-corrupted. The dev-loop `run_script_and_wait` also takes
   `script_path`.
8. **commit_slx runtime_vars require a non-empty `default`.** For a
   "must-supply" var use a sentinel like `REPLACE_ME` (regex-valid) so a
   forgotten override yields a clean "not found", not a crash.
9. **Secret-file encoding gotcha.** A creds file built with `printf %q` is
   correct for shell `source` but **corrupts special-char values when
   pasted into a non-shell secret UI** (auth then fails, e.g. SASL/SCRAM).
   Provide a separate RAW `KEY=value` file for the UI (`references/
   gen_env.sh` emits both).
10. **`run_slx` MCP poll caps at ~300 s and returns `status: timeout` —
    that is NOT task failure.** The RunSession keeps running server-side.
    Verify the real outcome via `get_run_sessions` and read the runRequest
    `requestTime`→`responseTime` (tasks often finish in seconds).
    `get_run_sessions` output is large → it auto-saves to a file; parse
    with python/jq, never inline.
11. **Verify schema/topology live before trusting docs or exploration.**
    Real DBs differ from notes (column names, table names,
    singular/plural, which DB, Redis key model). Confirm with a quick live
    query first. Empty Redis list queues don't exist as keys → "absent"
    can mean "healthy/drained", not "broken".
12. **Survive partial failure.** A triage SLX must never crash the
    contract: wrap each layer, convert failures into one issue, return the
    list. Time-box every external call; bound SCAN/log volume.

## Workflow

**Phase A — local-first (fastest iteration).**
1. Copy `references/slx_preamble.py` into a new `<task>.py`; write only
   `main()`.
2. `references/gen_env.sh` to mint local creds; point a kubecontext at the
   target cluster. Verify the live schema/topology with throwaway queries.
3. `references/run_local.sh <task>.py <args...>` until issues + stdout are
   right. Test the healthy, anomalous, and not-found paths.

**Phase B — remote validate, commit, verify (RunWhen MCP).**
4. (Once per cluster) `run_script_and_wait` with
   `references/context_probe.py` to pick `KUBE_CONTEXT`. Confirm the
   target services/namespace are visible.
5. `run_script_and_wait` the real task once (`script_path=…`,
   `secret_vars`, `env_vars={"KUBE_CONTEXT":…}`, `runtime_var_overrides`)
   — proves the runner can pip-install deps + port-forward + resolve
   secrets + reach the cluster.
6. `commit_slx` with `script_path=…`, `access`, `data`, `resource_path`,
   `hierarchy`, `interpreter="python"`, `task_type="task"`, `env_vars`
   (incl. `KUBE_CONTEXT`), `secret_vars`, `runtime_vars` (non-empty
   defaults). Read back the returned `runbook.yaml` to confirm config.
7. `run_slx` with `runtime_var_overrides`; if it reports `timeout`,
   confirm real success via `get_run_sessions` (see rule 10).

## `main()` skeleton (after the embedded preamble)

```python
def main():
    _ensure_deps([("psycopg[binary]", "psycopg")])   # add redis/neo4j/requests as needed
    if not preflight():
        return _ISSUES
    try:
        with pg("<db>", cred_prefix="MYAPP_PG") as conn:   # port-forward + connect
            ...                                            # query; add_issue() on anomalies
    except Exception as exc:                                # noqa: BLE001 — never crash the contract
        add_issue("X failed", f"{exc!r}", 2,
                  "Check reachability / secrets / port-forward RBAC.")
    section("VERDICT")
    print("  healthy" if not _ISSUES else f"  {len(_ISSUES)} issue(s)", flush=True)
    return _ISSUES
```

Deps the runner image may lack are pip-installed at runtime by
`_ensure_deps` (psycopg/redis/neo4j/requests). Reuse the preamble's
`port_forward`, `secret_val`, `kubeconfig_path`, `pg`, `q`, `show`,
`add_issue`, `section` verbatim so behavior stays proven and identical
across a task suite.

## commit_slx metadata cheatsheet

- `access`: `read-only` (agent can auto-run during investigate) or
  `read-write`. `data`: `logs-bulk` | `config` | `logs-stacktrace`.
- `resource_path`: `custom/<area>/<group>` (search/indexing).
  `hierarchy`: list → map-view grouping.
- Defer `resource_path` / `hierarchy` / secret discovery / location
  discovery specifics to the `configure-resource-path`,
  `configure-hierarchy`, `discover-secrets`, and `discover-locations`
  skills. This skill owns the infra-access and commit/run mechanics.
