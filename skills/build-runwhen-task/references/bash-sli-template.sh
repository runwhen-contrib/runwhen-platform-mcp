#!/bin/bash
# RunWhen Bash SLI Template
#
# SLI scripts return a health metric between 0 (unhealthy) and 1 (healthy).
# The value is written to file descriptor 3.
#
# Common patterns:
#   - Ratio: healthy_count / total_count (e.g. 8/10 pods ready = 0.8)
#   - Binary: 1.0 if check passes, 0.0 if it fails
#   - Percentage: convert percentage to 0-1 scale

set -euo pipefail

NAMESPACE="${NAMESPACE:-default}"
CONTEXT="${CONTEXT:-}"
export KUBECONFIG="${kubeconfig:-$HOME/.kube/config}"

main() {
    total=$(kubectl get pods -n "$NAMESPACE" ${CONTEXT:+--context="$CONTEXT"} \
        --no-headers --request-timeout=30s 2>/dev/null | wc -l | tr -d ' ')

    ready=$(kubectl get pods -n "$NAMESPACE" ${CONTEXT:+--context="$CONTEXT"} \
        --no-headers --request-timeout=30s \
        --field-selector=status.phase=Running 2>/dev/null | wc -l | tr -d ' ')

    if [[ "$total" -eq 0 ]]; then
        metric="1.0"
    else
        metric=$(echo "scale=2; $ready / $total" | bc)
    fi

    echo "Pod health: $ready/$total ready (metric=$metric)"
    echo "$metric" >&3
}
