# ===== SHARED-PREAMBLE START =====
import os
import sys
import socket
import time
import json
import subprocess
import contextlib

NS = os.environ.get("KUBE_NAMESPACE", "backend-services")
CTX = os.environ.get("KUBE_CONTEXT", "").strip()  # empty -> kubeconfig current-context

_ISSUES: list[dict] = []


def add_issue(title, desc, severity, next_steps, observed_at=None):
    issue = {
        "issue title": title,
        "issue description": desc,
        "issue severity": int(severity),
        "issue next steps": next_steps,
    }
    if observed_at:
        issue["issue observed at"] = observed_at
    _ISSUES.append(issue)
    # Always surface the issue in stdout too, so the task report captures
    # the signal even if issue indexing isn't consumed downstream.
    print(f"\n[ISSUE sev{int(severity)}] {title}\n  what: {desc}\n  "
          f"next: {next_steps}"
          + (f"\n  observed: {observed_at}" if observed_at else ""),
          flush=True)


def section(name):
    print(f"\n=== {name} ===", flush=True)


def _ensure_deps(mods):
    """Import modules; pip-install on first ImportError (runner image may lack them)."""
    missing = []
    for pip_name, import_name in mods:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print(f"[deps] installing: {missing}", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", *missing],
            check=False,
        )


def secret_val(env_name):
    """Secret vars are injected as FILE PATHS. Return the file's contents.
    Local-dev fallback: if the env value is not a path, treat it as the literal."""
    v = os.environ.get(env_name)
    if not v:
        return None
    try:
        if os.path.isfile(v):
            with open(v) as fh:
                return fh.read().strip()
    except OSError:
        pass
    return v.strip()


def kubeconfig_path():
    """kubeconfig secret -> a file path. Local-dev fallback to $KUBECONFIG/~/.kube/config."""
    v = os.environ.get("kubeconfig") or os.environ.get("KUBECONFIG")
    if v and os.path.isfile(v):
        return v
    home_kc = os.path.expanduser("~/.kube/config")
    return home_kc if os.path.isfile(home_kc) else (v or "")


KUBECONFIG = kubeconfig_path()


def kctl(*args, timeout=60, check=False):
    cmd = ["kubectl", "--kubeconfig", KUBECONFIG]
    if CTX:
        cmd += ["--context", CTX]
    cmd += ["-n", NS, *args]
    return subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=check)


def preflight():
    if not KUBECONFIG or not os.path.isfile(KUBECONFIG):
        add_issue("kubeconfig unavailable",
                  "The 'kubeconfig' secret did not resolve to a readable file. "
                  "Cannot reach the test cluster.", 1,
                  "Confirm the w-test workspace maps a 'kubeconfig' secret.")
        return False
    r = kctl("get", "ns", NS, "-o", "name")
    if r.returncode != 0:
        add_issue(f"Cannot access namespace {NS}",
                  f"`kubectl get ns {NS}` failed: {r.stderr.strip()[:400]}", 1,
                  "Verify the kubeconfig has get/list on namespace "
                  f"{NS} in the test cluster.")
        return False
    return True


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@contextlib.contextmanager
def port_forward(service, remote_port, ready_timeout=25):
    """kubectl port-forward svc/<service> <local>:<remote>; tears down on exit."""
    local = _free_port()
    cmd = ["kubectl", "--kubeconfig", KUBECONFIG]
    if CTX:
        cmd += ["--context", CTX]
    cmd += ["-n", NS, "port-forward", f"svc/{service}", f"{local}:{remote_port}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                err = (proc.stderr.read() or b"").decode()[:400]
                raise RuntimeError(f"port-forward {service} exited: {err}")
            try:
                with socket.create_connection(("127.0.0.1", local), timeout=1):
                    break
            except OSError:
                time.sleep(0.4)
        else:
            raise RuntimeError(f"port-forward {service} not ready in {ready_timeout}s")
        yield local
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def pg_conn(local_port, dbname, user, password):
    import psycopg
    return psycopg.connect(
        host="127.0.0.1", port=local_port, dbname=dbname,
        user=user, password=password, connect_timeout=15,
        prepare_threshold=None,  # pgbouncer transaction-pool safe
        autocommit=True, sslmode=os.environ.get("PG_SSLMODE", "require"),
    )


@contextlib.contextmanager
def pg(dbname, cred_prefix="TEST_PG"):
    """Yield a psycopg connection to <dbname> via core-pgbouncer port-forward."""
    user = secret_val(f"{cred_prefix}_USER")
    pw = secret_val(f"{cred_prefix}_PASSWORD")
    if not user or not pw:
        raise RuntimeError(
            f"Missing {cred_prefix}_USER / {cred_prefix}_PASSWORD workspace secret")
    with port_forward("core-pgbouncer", 5432) as lp:
        conn = pg_conn(lp, dbname, user, pw)
        try:
            yield conn
        finally:
            conn.close()


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        cols = [c.name for c in cur.description] if cur.description else []
        rows = cur.fetchall() if cur.description else []
    return cols, rows


def show(cols, rows, limit=50):
    if not rows:
        print("  (no rows)", flush=True)
        return
    for r in rows[:limit]:
        print("  " + " | ".join(
            f"{c}={'' if v is None else str(v)[:300]}" for c, v in zip(cols, r)),
            flush=True)
    if len(rows) > limit:
        print(f"  ... (+{len(rows) - limit} more rows)", flush=True)


def resolve_runsession(conn):
    rs = os.environ.get("RUNSESSION_ID", "").strip()
    if not rs:
        add_issue("RUNSESSION_ID not provided",
                  "The RUNSESSION_ID runtime variable was empty.", 2,
                  "Provide a RunSession numeric id or name slug.")
        return None, None
    cols, rows = q(conn,
                   "SELECT id, name FROM run_sessions "
                   "WHERE id::text = %s OR name = %s LIMIT 1", (rs, rs))
    if not rows:
        add_issue("RunSession not found",
                  f"No run_sessions row where id or name = '{rs}' "
                  "in the test 'core' DB.", 2,
                  "Confirm the RunSession id/name and that it exists.")
        return None, None
    rs_num, rs_name = rows[0][0], rows[0][1]
    print(f"Resolved RunSession: id={rs_num} name={rs_name}", flush=True)
    return rs_num, rs_name
# ===== SHARED-PREAMBLE END =====
