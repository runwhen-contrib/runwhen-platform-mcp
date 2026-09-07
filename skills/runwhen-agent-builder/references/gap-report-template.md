# Gap report template

The customer-facing half of the deliverable. It answers: *what would make this
agent more capable, and exactly what do you need to grant us?*

A build that produced three working tasks and five precisely specified missing
grants is a success. Vagueness is the failure mode — "needs more permissions"
is worthless; "needs `roles/certificatemanager.viewer` on project `X`, currently
denied with `PERMISSION_DENIED` on `certificatemanager.certs.list`" is actionable
by someone who has never spoken to us.

Location: `<env>/agent-builds/<use-case>-gaps.md`

---

## Template

```markdown
# <Use case> — capability gaps

Built on <workspace> at <date>. The agent covers <N> of <M> capabilities in the
original use case. Below is what is missing and what unlocks it.

## Working today

| Capability | Task | Credential used |
|---|---|---|
| Certificate inventory and expiry horizon | cert-expiry-inventory | ops-suite-sa |
| Per-certificate chain inspection | cert-chain-inspect | ops-suite-sa |

## Blocked — phase 2

### 1. Automated renewal

- **What it would do:** trigger ACME renewal when a certificate crosses 14 days.
- **Blocked by:** no write credential to the issuing CA.
- **Observed:** not attempted — no CA credential present in the workspace.
- **Unlocks with:** an ACME account key, or an API token with issue/renew scope
  on <CA>, stored as a workspace secret.
- **Then we can build:** a renewal task plus a post-renewal verification task.

### 2. Change-request creation

- **What it would do:** open a ServiceNow CRQ for each renewal window.
- **Blocked by:** no ServiceNow credential.
- **Observed:** not attempted.
- **Unlocks with:** a ServiceNow integration user with `create` on the change table.
- **Then we can build:** a CRQ task invoked from the renewal workflow.

### 3. Owner notification

- **What it would do:** email the named owner 30 days before expiry.
- **Blocked by:** owner field is empty on <N> of <M> certificates.
- **Observed:** <N> certificates carry no owner tag.
- **Unlocks with:** an authoritative owner source — a CMDB export, or an owner
  tag convention applied at issue time.
- **Then we can build:** ownership resolution inside the inventory task.

## Standing notes

**Break-glass task.** `break-glass-<platform>` accepts an arbitrary CLI
invocation as a runtime variable and is available to the agent. It can do
anything the underlying credential permits. It is deliberately excluded from the
assistant's task-tag filter and its description is narrowed so purpose-built
tasks win retrieval — but it remains the widest-blast-radius object in this
build. Remove it if the PoC becomes production.

**Schedule expiry.** `max_runs` is capped at 30 by the platform. The scheduled
command `<name>` will stop firing after 30 successful runs (roughly six weeks on
a weekday schedule) and must be reset with `update_chat_command`. This is a
platform limit, not a configuration choice.
```

---

## Rules for filling it in

- **Observed, not assumed.** If Phase 1 hit a permission denial, quote it. If a
  capability was never attempted because no credential existed at all, say
  "not attempted" rather than implying a test.
- **Name the grant, not the category.** A role name, a scope, a token type.
- **Say what it unlocks.** A grant with no stated payoff does not get approved.
- **Always carry both standing notes.** Every build has a break-glass task and a
  `max_runs` ceiling; both outlive the memory of whoever ran the build.
