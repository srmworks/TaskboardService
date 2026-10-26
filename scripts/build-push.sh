#!/usr/bin/env bash
# Build both images and push them to Docker Hub.
# Usage: scripts/build-push.sh [version]   (requires: docker login)
set -euo pipefail

HUB="${HUB:-srmworks}"
VERSION="${1:-1.0.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for svc in task-service web-gateway; do
  image="docker.io/${HUB}/taskboard-${svc}:${VERSION}"
  echo ">> Building ${image}"
  docker build -t "${image}" "${ROOT}/${svc}"
  echo ">> Pushing ${image}"
  docker push "${image}"
done

echo "Done. If you used a version other than 1.0.0, update the image tags in k8s/30-*.yaml and k8s/40-*.yaml."
