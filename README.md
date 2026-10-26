# TaskBoard

Taskboard service application for Kubernetes (minikube): a web to-do list with live statistics.

```
Browser -> Ingress (nginx) -> web-gateway (x2) --REST--> task-service (x2) --SQL--> PostgreSQL (StatefulSet + PVC)
```

| Microservice | Image | Port | Role |
|---|---|---|---|
| web-gateway | `docker.io/srmworks/taskboard-web-gateway:1.0.0` | 8080 | Web UI and `/api/*`. Consumes the task-service REST API and computes the statistics |
| task-service | `docker.io/srmworks/taskboard-task-service:1.0.0` | 8000 | Task CRUD REST API. The only service with database access |
| postgres | `postgres:16-alpine` | 5432 | Persistent storage (1 replica, 1Gi PVC) |

## Repository layout

```
task-service/   Flask REST API + Dockerfile
web-gateway/    Flask gateway, templates/index.html + Dockerfile
k8s/            Kubernetes manifests (applied in numeric order)
scripts/        build-push.sh, deploy.sh
docker-compose.yaml  local smoke test
```

## 1. Build and push the images (inside the VM)

```bash
docker login -u srmworks
scripts/build-push.sh 1.0.0
```

Local smoke test before pushing:

```bash
docker compose up --build           # open http://localhost:8080
docker compose down -v
```

## 2. Deploy to minikube

```bash
minikube start --driver=docker --cpus=2 --memory=4g --cni=calico
minikube addons enable ingress
minikube addons enable metrics-server 

scripts/deploy.sh                 # --no-netpol if not using calico
```

`deploy.sh` creates the `taskboard` namespace and a `postgres-secret` with a random password. It then applies the manifests in order and waits for each rollout. `k8s/10-postgres-secret.yaml.example` documents the Secret's format only, and is never applied.

Open `http://$(minikube ip)/` in the VM's browser.

## 3. REST API

Public API (through the Ingress, web-gateway):

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/tasks` | – | list of tasks |
| POST | `/api/tasks` | `{"title": "..."}` | 201, created task |
| PUT | `/api/tasks/<id>` | `{"title"?: "...", "done"?: true}` | updated task |
| DELETE | `/api/tasks/<id>` | – | 204 |
| GET | `/api/stats` | – | `{total, done, pending, completion_rate}` |
| GET | `/health` | – | gateway health |

The internal API (task-service, cluster-only) is the same set of operations under `/tasks`, plus `/health`, which also checks the database.

Responses include `served_by` (the task-service pod) and `gateway` (the web-gateway pod), so you can see load balancing across replicas.

```bash
BASE=http://$(minikube ip)
curl -s -X POST $BASE/api/tasks -H 'Content-Type: application/json' -d '{"title":"Write report"}'
curl -s $BASE/api/tasks
curl -s -X PUT $BASE/api/tasks/1 -H 'Content-Type: application/json' -d '{"done":true}'
curl -s $BASE/api/stats
curl -s -X DELETE $BASE/api/tasks/1 -o /dev/null -w '%{http_code}\n'
```


## 4. Clean up

```bash
kubectl delete namespace taskboard     # also deletes the PVC and its data
```
