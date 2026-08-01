#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: deploy_release.sh <previous_sha> <target_sha> <deploy_web:true|false> [services...]" >&2
  exit 2
fi

PREVIOUS_SHA="$1"
TARGET_SHA="$2"
DEPLOY_WEB="$3"
shift 3
SERVICES=("$@")

DEPLOY_DIR="${DEPLOY_DIR:-/home/stockapp/stock-analyzer}"
VENV="${VENV:-$DEPLOY_DIR/venv}"
TARGET_TAG="$TARGET_SHA"
ROLLBACK_TAG="rollback-${PREVIOUS_SHA:0:12}"

declare -A OLD_IMAGE_IDS=()

cd "$DEPLOY_DIR"

for service in "${SERVICES[@]}"; do
  container_id="$(docker compose ps -q "$service")"
  if [ -z "$container_id" ]; then
    echo "ERROR: $service is not running; refusing to start an unexpected service" >&2
    exit 1
  fi
  OLD_IMAGE_IDS["$service"]="$(docker inspect "$container_id" --format '{{.Image}}')"
done

rollback() {
  status=$?
  trap - ERR
  echo "Deployment failed; rolling back to $PREVIOUS_SHA" >&2

  sudo -u stockapp git -C "$DEPLOY_DIR" reset --hard "$PREVIOUS_SHA" || true

  if [ "$DEPLOY_WEB" = "true" ]; then
    sudo -u stockapp "$VENV/bin/pip" install -r "$DEPLOY_DIR/requirements.txt" -q || true
    systemctl restart stock-analyzer || true
  fi

  for service in "${SERVICES[@]}"; do
    old_image="${OLD_IMAGE_IDS[$service]:-}"
    if [ -n "$old_image" ]; then
      docker tag "$old_image" "stock-analyzer-${service}:$ROLLBACK_TAG" || true
      APP_IMAGE_TAG="$ROLLBACK_TAG" docker compose up -d --no-deps --force-recreate "$service" || true
    fi
  done
  exit "$status"
}
trap rollback ERR

# Build every target before replacing any running container. A build failure
# therefore cannot interrupt a healthy ingestion process.
for service in "${SERVICES[@]}"; do
  APP_IMAGE_TAG="$TARGET_TAG" docker compose build \
    --build-arg "APP_GIT_SHA=$TARGET_SHA" "$service"
done

if [ "$DEPLOY_WEB" = "true" ]; then
  sudo -u stockapp "$VENV/bin/pip" install -r "$DEPLOY_DIR/requirements.txt" -q
  systemctl restart stock-analyzer
  systemctl is-active --quiet stock-analyzer
fi

for service in "${SERVICES[@]}"; do
  APP_IMAGE_TAG="$TARGET_TAG" docker compose up -d --no-deps --force-recreate "$service"
done

for service in "${SERVICES[@]}"; do
  container_id="$(docker compose ps -q "$service")"
  [ -n "$container_id" ]
  [ "$(docker inspect "$container_id" --format '{{.State.Status}}')" = "running" ]
  revision="$(docker inspect "$container_id" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  if [ "$revision" != "$TARGET_SHA" ]; then
    echo "ERROR: $service revision $revision does not match $TARGET_SHA" >&2
    false
  fi
done

trap - ERR
echo "Deployment verified at commit $TARGET_SHA; services: ${SERVICES[*]:-(source only)}"
