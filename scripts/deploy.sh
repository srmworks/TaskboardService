#!/usr/bin/env bash
# Deploy TaskBoard to the current kubectl context (minikube).
# Usage: scripts/deploy.sh [--no-netpol]
set -euo pipefail

K8S="$(cd "$(dirname "$0")/../k8s" && pwd)"
NS=taskboard
NETPOL=1
for arg in "$@"; do
  case "$arg" in
    --no-netpol) NETPOL=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

kubectl apply -f "${K8S}/00-namespace.yaml"

# Create the DB secret once with a random password (never stored in Git).
if ! kubectl -n "$NS" get secret postgres-secret >/dev/null 2>&1; then
  echo ">> Creating postgres-secret with a random password"
  kubectl -n "$NS" create secret generic postgres-secret \
    --from-literal=POSTGRES_USER=taskboard \
    --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 18)" \
    --from-literal=POSTGRES_DB=taskboard
fi

kubectl apply -f "${K8S}/20-postgres.yaml"
kubectl -n "$NS" rollout status statefulset/postgres --timeout=180s

kubectl apply -f "${K8S}/30-task-service.yaml" -f "${K8S}/40-web-gateway.yaml"
kubectl -n "$NS" rollout status deployment/task-service --timeout=180s
kubectl -n "$NS" rollout status deployment/web-gateway --timeout=180s

kubectl apply -f "${K8S}/50-ingress.yaml"
[[ $NETPOL == 1 ]] && kubectl apply -f "${K8S}/60-networkpolicy.yaml"

kubectl -n "$NS" get pods,svc,pvc,ingress -o wide
echo
echo "Open: http://$(minikube ip)/   (may take ~30s until the Ingress gets an address)"
