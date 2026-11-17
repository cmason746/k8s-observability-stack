# Shortcuts for running the stack locally. See README.md.
.PHONY: up down grafana prometheus

up:          ## Create the cluster and deploy everything
	./scripts/up.sh

down:        ## Delete the cluster
	./scripts/down.sh

grafana:     ## Open a tunnel to Grafana on http://localhost:3000
	kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80

prometheus:  ## Open a tunnel to Prometheus on http://localhost:9090
	kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090
