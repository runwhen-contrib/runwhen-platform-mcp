# Building the investigation surface

Phase 2, **step one** — before any filter runs.

The question this answers: *what would a complete investigation of this use case
actually require?* Not what the workspace has. Not what you happen to remember.

## Why this step exists

Existing coverage tells you what you **have**. It cannot tell you what **complete**
looks like. And your own recall is the wrong source: measured skill gains are
largest exactly where a model's knowledge is sparse and procedural (+52 points in
domains like healthcare against +5 in software engineering). The procedural
detail that makes a use case investigable — the order to walk a resource chain,
the failure mode that is invisible at the layer you are looking at — is precisely
the knowledge least likely to be recalled correctly and most worth encoding.

So derive the surface from **authoritative external references**, then subtract
what exists.

## Method

**1. Research per platform in scope.** For each platform the credentials reach
(Kubernetes, GCP, Azure, AWS, a database, a SaaS API), search for that platform's
own troubleshooting documentation and real failure reports on this use case.
Vendor docs, project docs, issue trackers, incident write-ups. Prefer the
project's own troubleshooting guide over blog summaries.

Query shapes that work:

```
<platform> <use case> troubleshooting runbook
<platform> <use case> failure modes production issue
<use case> audit checklist
<resource> stuck pending / not ready / not updating
```

**2. Extract the surface as investigation steps, not topics.** Each entry is
something a task could actually do. Record for each: what it inspects, what
failure it detects, and — critically — **which layer it lives at**, because the
common trap is a use case that looks covered at one layer while failing at
another.

**3. Split breadth from depth.** A step is *breadth* if it must run across the
whole estate to be useful ("which of everything is closest to failing"). It is
*depth* if it only makes sense once you have a specific subject.

**4. Build the coverage matrix.** One row per investigation step. Columns:
deployed SLX / registry bundle / **GAP**. Only now do the five filters run, and
they run against the GAP rows.

```
STEP                                    LAYER          B/D  COVERED BY
Namespace certificate summary           Certificate     D   slx k8s-certmanager-healthcheck
Unhealthy certificates in namespace     Certificate     D   slx k8s-certmanager-healthcheck
Failed CertificateRequests              CertReq         D   slx k8s-certmanager-healthcheck
Cross-namespace expiry horizon          Certificate     B   *** GAP ***
ACME Order/Challenge chain walk         Order/Challenge D   *** GAP ***
Served-vs-declared certificate drift    Endpoint        B   *** GAP ***
Chain completeness (intermediates)      Secret bytes    D   *** GAP ***
SAN coverage vs ingress hosts           Ingress         D   *** GAP ***
```

**5. Report the surface, not just the plan.** The matrix goes to the user at the
Phase 2 gate and into the gap report. A customer who is told "you have
certificate monitoring" is being misled if it stops at one layer.

## The layer trap

The most valuable gaps are almost always **cross-layer**: the thing that reports
healthy at layer N while failing at layer N+1.

Worked example, certificates. cert-manager can report a `Certificate` as `Ready`
while the endpoint serves the *old* bytes — the renewed secret is in etcd, but
the ingress controller or service mesh never reloaded and is still holding the
previous certificate in memory. Every `Certificate`-layer check passes. Users
still see an expired certificate. A related family: the referenced secret is in
the wrong namespace or missing, so the controller silently serves its built-in
fake certificate; or `tls.crt` omits intermediates, because Kubernetes does not
append them for you, so some clients fail while your checks pass.

No amount of inspecting `Certificate` objects finds any of these. Only comparing
**declared state against what the endpoint actually presents** does — which is
why a served-vs-declared task is worth more than another status check.

When you build the matrix, ask of every use case: *what is the layer below the
one everything already watches, and what fails silently there?*

## Anti-patterns

| Don't | Do |
|---|---|
| Derive the surface from memory | Search the platform's own troubleshooting docs |
| Treat "existing task exists" as "covered" | Ask which *layer* it covers |
| List topics ("check certificates") | List steps ("walk Certificate → CertificateRequest → Order → Challenge and report the first blocked resource") |
| Research one platform when credentials reach three | One pass per reachable platform |
| Hide the uncovered rows | The matrix is a deliverable |
