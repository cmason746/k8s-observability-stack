#!/usr/bin/env bash
# Delete the kind cluster and everything in it. The repo has everything needed
# to bring it back with `make up`.
set -euo pipefail

CLUSTER="${CLUSTER:-lab}"

if kind get clusters | grep -qx "$CLUSTER"; then
  kind delete cluster --name "$CLUSTER"
else
  echo "No kind cluster called '$CLUSTER'"
fi
