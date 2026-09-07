"""Throwaway discovery probe. Adapt, run, then DELETE — never commit this.

Run with:
    run_script_and_wait(interpreter="python", run_type="task",
                        script_path="<this file>",
                        env_vars={...}, secret_vars={...})

Its only job is to answer, against the REAL environment:
  1. Who am I, and what can I reach?
  2. Which candidate data sources respond, and which are empty or forbidden?
  3. How far back does history go?
  4. What ceilings exist, and where are they declared?
  5. What already exists in the workspace to attach to?

Expect the first run to fail on auth or filter syntax. That is the probe
working. Iterate until you have facts, not guesses.

Two modes, selected by the PROBE_MODE env var:
  full    (default)  the whole discovery pass; writes probe/findings.md
  verify             the cheap subset used on session resume - identity, one
                     query per source, the retention edge. Prints a FINGERPRINT
                     block to diff against the one findings.md recorded. Any
                     difference means the probe is stale and must be re-run in
                     full. See references/build-ledger.md.

The RunWhen contract requires main() to return a list of issues; returning []
is fine for a probe since everything useful goes to stdout.
"""
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


def sh(args, timeout=120):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout or "", (r.stderr or "").strip(), r.returncode
    except Exception as exc:  # noqa: BLE001
        return "", str(exc)[:300], -1


def section(title):
    print("\n" + "=" * 68)
    print(f"##### {title}")
    print("=" * 68)


def verify():
    """Cheap staleness check for a resumed session.

    Not a second probe. It answers one question: is anything different from
    what findings.md recorded? Identity first, because a changed principal
    invalidates every permission conclusion in the file at once.

    Print the same fields in the same order every time — the value of this
    output is that it diffs cleanly against the stored fingerprint.
    """
    section("FINGERPRINT")

    out, err, _ = sh(["gcloud", "config", "list", "--format=value(core.account)"])
    print("  identity.gcloud   %s" % (out.strip() or err[:120] or "(none)"))
    out, err, _ = sh(["kubectl", "auth", "whoami", "-o", "jsonpath={.status.userInfo.username}"])
    print("  identity.k8s      %s" % (out.strip() or err[:120] or "(none)"))

    # One liveness call per source the design depends on. Keep these cheap and
    # keep the list identical to the sources recorded in findings.md.
    for label, cmd in [
        ("k8s.nodes", ["kubectl", "get", "nodes", "-o", "name"]),
        # ("gcs.buckets", ["gcloud", "storage", "ls", "--format=value(name)"]),
    ]:
        out, err, rc = sh(cmd)
        n = len([x for x in out.splitlines() if x.strip()])
        print(f"  source.{label:<11} rc={rc} count={n} {err[:80]}")

    # The retention edge: the oldest window that still returns data. If this
    # moved, every trend claim in the design needs rechecking.
    for days in (90, 365):
        points, note = _probe_history(days)
        print(f"  retention.{days:<3}d    points={points} {note}")

    print("\n##### VERIFY COMPLETE — diff against findings.md; any difference"
          " means re-probe in full")
    return []


def main():
    if os.environ.get("PROBE_MODE", "full").lower() == "verify":
        return verify()

    # ---- 1. IDENTITY -------------------------------------------------------
    # Never assume which principal you are. Permissions and quota-project
    # behaviour both hang off this, and it is rarely the one you expect.
    section("IDENTITY")
    for label, cmd in [
        ("gcloud account", ["gcloud", "config", "list", "--format=json"]),
        ("gcloud auth", ["gcloud", "auth", "list", "--format=json"]),
        ("kubectl whoami", ["kubectl", "auth", "whoami"]),
    ]:
        out, err, rc = sh(cmd)
        detail = (out or err)[:300].replace("\n", " ")
        print(f"  {label:<16} rc={rc} {detail}")
    print("  serviceaccount ns:", _read("/var/run/secrets/kubernetes.io/"
                                        "serviceaccount/namespace"))

    # ---- 2. WHAT EXISTS ----------------------------------------------------
    # List before you filter. A namespace you assumed exists may not, and a
    # documented component may have been removed without the docs changing.
    section("WHAT ACTUALLY EXISTS")
    out, err, rc = sh(["kubectl", "get", "ns", "-o", "name"])
    print("  namespaces:", ", ".join(sorted(
        x.split("/")[-1] for x in (out or "").split())) if rc == 0 else err[:200])

    # Probe each candidate source for BOTH reachability and emptiness.
    # A source that answers with zero rows is not a source.
    for ns in os.environ.get("CANDIDATE_NAMESPACES", "").split(","):
        ns = ns.strip()
        if not ns:
            continue
        out, err, rc = sh(["kubectl", "get", "svc", "-n", ns, "--no-headers"])
        n = len((out or "").strip().splitlines()) if rc == 0 else -1
        err_note = "" if rc == 0 else err[:120]
        print(f"  [{ns}] services={n} {err_note}")

    # ---- 3. PERMISSIONS THAT COMMONLY BITE ---------------------------------
    section("PERMISSION SPOT-CHECKS")
    for verb, res in [("get", "nodes"), ("get", "nodes/proxy"),
                      ("list", "secrets"), ("get", "pods")]:
        out, _, _ = sh(["kubectl", "auth", "can-i", verb, res])
        print(f"  can-i {verb:<6} {res:<14} -> {(out or '?').strip()}")

    # ---- 4. RETENTION LADDER ----------------------------------------------
    # THE decisive question for anything trend-shaped. Test progressively
    # further back; "the API responds" says nothing about how much history
    # you actually have.
    section("RETENTION LADDER")
    for days in [1, 7, 30, 90, 180, 365]:
        n, note = _probe_history(days)
        print(f"   -{days:>4}d  datapoints={n:<8} {note}")

    # ---- 5. CEILINGS -------------------------------------------------------
    # Whatever the requirement is about running out of, find where the limit
    # is declared. Headroom without a stated limit is not a finding.
    section("CEILINGS / LIMITS")
    print("  TODO: query the limits this requirement implies —")
    print("        autoscaler maxima, quota limits, provisioned capacity,")
    print("        expiry dates, plan tiers. Note WHERE each is declared.")

    print("\n##### PROBE COMPLETE — record findings, then delete this script")
    return []


def _read(path):
    try:
        return open(path).read().strip()
    except Exception:  # noqa: BLE001
        return "(unavailable)"


def _probe_history(days_ago):
    """Adapt to the metrics backend you are probing.

    GOTCHA (GCP): the quota-project header must name the project the RUNNER'S
    service account lives in, which is often NOT the project you are querying.
    Getting it wrong returns 403 on every metric and looks like "no access".
    Omitting the header entirely also works.
    """
    project = os.environ.get("GCP_PROJECT", "")
    quota_project = os.environ.get("GCP_QUOTA_PROJECT", "")
    metric = os.environ.get("PROBE_METRIC", "")
    if not (project and metric):
        return "n/a", "set GCP_PROJECT + PROBE_METRIC to use this section"

    token, _, rc = sh(["gcloud", "auth", "print-access-token"], timeout=90)
    if rc != 0:
        return "0", "no access token"

    end = datetime.now(timezone.utc) - timedelta(days=days_ago)
    start = end - timedelta(hours=6)
    params = {
        "filter": f'metric.type="{metric}"',
        "interval.startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval.endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregation.alignmentPeriod": "3600s",
        # gauges need ALIGN_MEAN; cumulative metrics need ALIGN_RATE
        "aggregation.perSeriesAligner": "ALIGN_MEAN",
    }
    url = ("https://monitoring.googleapis.com/v3/projects/"
           f"{project}/timeSeries?") + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer " + token.strip()}
    if quota_project:
        headers["x-goog-user-project"] = quota_project
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        ts = d.get("timeSeries", [])
        return sum(len(s.get("points", [])) for s in ts), f"series={len(ts)}"
    except urllib.error.HTTPError as exc:
        body = exc.read(200).decode("utf-8", "replace").replace("\n", " ")
        return "0", f"HTTP {exc.code} {body[:150]}"
    except Exception as exc:  # noqa: BLE001
        return "0", str(exc)[:150]
