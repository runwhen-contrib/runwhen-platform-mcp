#!/bin/bash
# RunWhen Bash Task Template
#
# This is a complete, contract-compliant bash task script.
# Copy and adapt for your specific health check.
#
# Contract requirements:
#   - Define main() function (runner invokes it)
#   - Write issue JSON array to file descriptor 3 (>&3)
#   - Use jq for reliable JSON construction
#   - Never call main() directly
#
# Secrets are injected as file paths via env vars.
# Example: $kubeconfig is a path — use export KUBECONFIG=$kubeconfig

set -euo pipefail

# --- Configuration from env vars ---
NAMESPACE="${NAMESPACE:-default}"
CONTEXT="${CONTEXT:-}"
export KUBECONFIG="${kubeconfig:-$HOME/.kube/config}"

main() {
    issues='[]'

    # Example: check for pods not in Running state
    not_ready=$(kubectl get pods -n "$NAMESPACE" ${CONTEXT:+--context="$CONTEXT"} \
        --no-headers --request-timeout=30s \
        --field-selector=status.phase!=Running,status.phase!=Succeeded 2>/dev/null || true)

    if [[ -n "$not_ready" ]]; then
        pod_count=$(echo "$not_ready" | wc -l | tr -d ' ')
        pod_list=$(echo "$not_ready" | awk '{print $1}' | head -5 | tr '\n' ', ' | sed 's/,$//')

        issues=$(jq -n \
            --arg title "$pod_count pod(s) not ready in namespace $NAMESPACE" \
            --arg desc "Pods not in Running/Succeeded state: $pod_list" \
            --arg severity "2" \
            --arg steps "Run: kubectl describe pod <name> -n $NAMESPACE to check events and conditions" \
            '[{
                "issue title": $title,
                "issue description": $desc,
                "issue severity": ($severity | tonumber),
                "issue next steps": $steps
            }]')
    fi

    # Print diagnostic info to stdout (appears in report)
    echo "Checked namespace: $NAMESPACE"
    echo "Pods found not ready: $(echo "$issues" | jq length)"

    # Write issues to FD 3 (required by contract)
    echo "$issues" >&3
}
