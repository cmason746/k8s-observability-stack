# Kubernetes observability stack

A small online shop running on Kubernetes, with Prometheus and Grafana monitoring around it.

**Stack:** Kubernetes (kind) · Helm · Prometheus · Grafana · Python (FastAPI) · Redis · Docker

It runs on a local kind cluster: four apps (an orders API, an inventory service, Redis and a load generator) across six pods, plus a CronJob that restocks inventory every five minutes. The monitoring stack is installed with Helm.

The services expose RED metrics (rate, errors, duration) and business metrics, the load generator keeps realistic traffic flowing, and a Grafana dashboard shows service health, stock levels and each pod's resource use against its requests and limits. The whole stack comes up from a fresh clone with `make up`.

![Shop service overview dashboard](docs/images/dashboard.png)

## Architecture

```mermaid
flowchart TB
  subgraph monitoring["namespace: monitoring"]
    dash["dashboard ConfigMap"] -.->|loaded by sidecar| grafana["Grafana"]
    grafana -->|PromQL| prom["Prometheus"]
    operator["Prometheus Operator"] -.->|scrape config| prom
    ksm["kube-state-metrics"]
    ne["node-exporter<br/>DaemonSet"]
  end

  subgraph shop["namespace: shop"]
    loadgen["loadgen<br/>Deployment"] -->|HTTP| orders["orders API<br/>Deployment ×2"]
    restock["restock<br/>CronJob, every 5 min"]
    orders -->|HTTP| inventory["inventory<br/>Deployment ×2"]
    restock -->|POST /restock| inventory
    inventory --> redis[("Redis<br/>Deployment")]
    sm["ServiceMonitor"]
  end

  subgraph node["every node"]
    kubelet["kubelet<br/>with cAdvisor"]
  end

  sm -.->|read by| operator
  prom -->|scrapes /metrics| orders
  prom -->|scrapes /metrics| inventory
  prom -->|scrapes| ksm
  prom -->|scrapes| ne
  prom -->|scrapes| kubelet
```

- **Traffic** flows from the load generator to orders, which calls inventory, which keeps the stock in Redis. Every call uses a Service name, resolved by cluster DNS.
- **Metrics are pulled.** Prometheus scrapes each pod's `/metrics` endpoint every 15 seconds. The ServiceMonitor tells it which pods to scrape, and pod CPU and memory come from the kubelet's built-in cAdvisor.
- **Grafana** queries Prometheus. The dashboard is a JSON file in this repo, loaded into Grafana from a ConfigMap.

## What's in it

All three apps are small Python (FastAPI) services.

| Component | What it does | Kubernetes objects |
|---|---|---|
| **orders** | The shop's API: lists products and places orders by calling inventory | Deployment (2 replicas), Service, ConfigMap |
| **inventory** | Owns the stock, kept in Redis, and reserves it in one atomic step so two orders can't take the last item | Deployment (2 replicas), Service, ConfigMap |
| **Redis** | The shared stock store, password-protected | Deployment, Service, Secret |
| **restock** | Resets the stock every 5 minutes, like a delivery arriving | CronJob |
| **loadgen** | Sends about 2 requests a second, including some deliberately invalid ones | Deployment, ConfigMap |
| **kube-prometheus-stack** | Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics and the Prometheus Operator | Helm release |
| **shop ServiceMonitor** | Tells Prometheus which pods to scrape: those behind the orders and inventory Services | ServiceMonitor (custom resource) |
| **Shop dashboard** | RED metrics, business metrics, and pod CPU and memory | ConfigMap, loaded by Grafana's sidecar |


## Design decisions

**Health checks**
- **Readiness checks dependencies, liveness doesn't.** Inventory's readiness probe pings Redis, so when Redis is down its pods stop receiving traffic without being restarted, since a restart wouldn't fix Redis. Liveness only checks that the process answers, so a dependency blip can't restart every pod.
- **Orders' readiness doesn't check inventory**, to avoid a cascading failure where an inventory outage takes orders out of service too. Scaling Redis to zero shows the result: within about 15 seconds inventory's pods go not ready and the Service stops sending them traffic, so inventory's request rate falls to zero, while orders stays up and returns clean 503s. The errors show up at the caller, and the broken service just goes quiet, which is why a drop in request rate is worth watching as well as the error rate.

**Data and config**
- **Redis is a Deployment with no persistent storage**, to keep the focus on the monitoring. Inventory reseeds the starting stock whenever Redis comes back empty. A production database would be a StatefulSet with persistent storage.
- **The Redis password is generated at setup and never committed.** `make up` creates the Secret with a random value, only if it doesn't already exist.

**Metrics**
- **RED metrics are labelled with the route template, never the raw URL**, and requests that match no route are labelled `unmatched`, so cardinality stays bounded. Probe and scrape requests are left out of the metrics and the access logs.
- **5xx counts as an error, and 4xx is shown separately.** The load generator sends invalid orders on purpose, and those are the caller's mistake, not a service failure.

**Traffic and resources**
- **The load generator opens a new connection for each request.** With keep-alive, every request went to one orders pod, because a Service picks a pod once per connection. Separate connections behave like separate users and spread the load.
- **Requests and limits were right-sized from the dashboard.** The load generator used about a quarter of its memory request, so the request was cut from 64Mi to 32Mi.

**Reproducibility**
- **The dashboard is code.** Its JSON lives in this repo and is loaded through a ConfigMap labelled for Grafana's sidecar, so it survives Grafana restarts and appears on a fresh cluster.

## Screenshots

Prometheus scraping both orders pods and both inventory pods through the ServiceMonitor:

![Prometheus targets](docs/images/targets.png)

The dashboard a couple of minutes after Redis is scaled to zero. Orders keeps serving and returns 503s, while inventory stops receiving traffic. Inventory's error rate is only worked out from the few requests it handled before going not ready, so it climbs as its earlier successful requests age out of the 5-minute window:

![Dashboard during a Redis outage](docs/images/redis-outage.png)

## Running it

You need Docker (Docker Desktop, Colima or similar, with about 4 CPUs and 8 GB of memory), [kind](https://kind.sigs.k8s.io/), kubectl and Helm. On macOS: `brew install kind kubectl helm`.

```bash
make up          # create the cluster and deploy everything (about 5 minutes)
make grafana     # in a second terminal, then open http://localhost:3000/d/shop-overview
make prometheus  # optional, then open http://localhost:9090
make down        # delete the cluster
```

Log in to Grafana as `admin`. The password is generated by the chart:

```bash
kubectl get secret monitoring-grafana -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

### Try breaking it

Take Redis away, watch the dashboard for a few minutes, then bring it back:

```bash
kubectl scale deployment redis -n shop --replicas=0
kubectl scale deployment redis -n shop --replicas=1
```

## Repo layout

```
services/     App code and Dockerfiles: orders, inventory, loadgen
k8s/          Kubernetes manifests, one folder per app, plus the namespace and ServiceMonitor
helm/         Values for the kube-prometheus-stack chart
dashboards/   Grafana dashboard JSON
scripts/      up.sh and down.sh, run by make
```

## What's next

- **Traces with OpenTelemetry.** An OpenTelemetry Collector as a DaemonSet (an agent on each node) feeding a gateway Deployment, the OpenTelemetry Operator injecting auto-instrumentation into the Python services, Tempo for traces linked from Grafana, and tail sampling at the gateway.
- **Logs, SLOs and alerting.** Loki for logs, with trace IDs so Grafana can jump between logs, metrics and traces. An SLO per service with multi-window burn-rate alerts routed through Alertmanager.
- **Redis as a StatefulSet** with persistent storage.
- **Cloud and GitOps.** Terraform to run the same stack on EKS, ArgoCD for deployment from this repo, and GitHub Actions to build images and validate manifests.
