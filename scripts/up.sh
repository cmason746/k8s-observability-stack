#!/usr/bin/env bash
# Bring the whole stack up on a local kind cluster, from nothing.
# Safe to run again: every step creates what's missing and updates what exists.
#
#   make up            (or ./scripts/up.sh)
#   CLUSTER=demo make up   to use a different cluster name
set -euo pipefail

CLUSTER="${CLUSTER:-lab}"
KPS_CHART_VERSION="93.3.0"   # kube-prometheus-stack chart version
NODE_IMAGE="kindest/node:v1.37.0"   # Kubernetes version of the kind node
SERVICES=(orders inventory loadgen)

# Run from the repo root, wherever the script is called from
cd "$(dirname "$0")/.."

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

step "Checking tools"
for tool in docker kind kubectl helm openssl; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool. Install it first (see README)."; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker isn't running. Start it first (for example: colima start)."; exit 1; }

step "Kind cluster '$CLUSTER'"
if kind get clusters | grep -qx "$CLUSTER"; then
  echo "Already exists"
else
  kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE"
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

step "Building and loading app images"
# The image tag is read from each Deployment, so the YAML is the only place versions live
for svc in "${SERVICES[@]}"; do
  image=$(awk '/image:/ {print $2; exit}' "k8s/$svc/deployment.yaml")
  echo "--- $image"
  docker build -q -t "$image" "services/$svc"
  kind load docker-image "$image" --name "$CLUSTER"
done

step "Monitoring stack (kube-prometheus-stack $KPS_CHART_VERSION)"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
helm repo update prometheus-community >/dev/null
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version "$KPS_CHART_VERSION" -n monitoring --create-namespace \
  -f helm/kube-prometheus-stack/values.yaml --wait --timeout 10m

step "Shop namespace and Redis password"
kubectl apply -f k8s/namespace.yaml
# Generated here and never committed. Kept if it already exists, so Redis and
# inventory keep agreeing on it.
if kubectl get secret redis-auth -n shop >/dev/null 2>&1; then
  echo "Secret redis-auth already exists"
else
  kubectl create secret generic redis-auth -n shop --from-literal=password="$(openssl rand -hex 16)"
fi

step "Shop services"
kubectl apply -f k8s/redis/ -f k8s/inventory/ -f k8s/orders/ -f k8s/loadgen/
kubectl apply -f k8s/shop-servicemonitor.yaml

step "Grafana dashboard"
# Generated from the JSON file, which is the source of truth. The label makes
# Grafana's sidecar load it.
kubectl create configmap shop-dashboard -n monitoring \
  --from-file=dashboards/shop-overview.json --dry-run=client -o yaml \
  | kubectl label --local -f - grafana_dashboard=1 -o yaml \
  | kubectl apply -f -

step "Waiting for the shop services to be ready"
for deploy in redis inventory orders loadgen; do
  kubectl rollout status "deployment/$deploy" -n shop --timeout=180s
done

step "Done"
cat <<EOF

Everything is up. The load generator is sending traffic, so give the
dashboard a few minutes to fill in.

  Grafana:     make grafana      then open http://localhost:3000/d/shop-overview
               user admin, password:
               kubectl get secret monitoring-grafana -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d; echo
  Prometheus:  make prometheus   then open http://localhost:9090

  Tear down:   make down
EOF
