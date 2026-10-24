#!/bin/sh
# The simulated production host for scripts/rehearse_install.py (ADR-0032). Runs inside
# `unshare --map-auto --map-root-user --net --mount --cgroup`: root in a user namespace,
# with a network namespace that has only a loopback interface.
#
#   rehearse_install.sh WORK BUNDLE UPGRADE_BUNDLE ALLOWED_SIGNERS
set -u
W=$1
A=$2
B=$3
SIGNERS=$4
# ---- the simulated host: fresh /run, /sys and cgroups, loopback only
mount -t tmpfs none /run
mkdir -p /run/lock /run/podman /run/systemd/resolve
echo "nameserver 192.0.2.53" > /run/systemd/resolve/stub-resolv.conf
mount -t sysfs none /sys
mount -t cgroup2 none /sys/fs/cgroup
mkdir -p /sys/fs/cgroup/init
for p in $(cat /sys/fs/cgroup/cgroup.procs); do echo "$p" > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || true; done
echo "+cpu +memory +pids" > /sys/fs/cgroup/cgroup.subtree_control
ip link set lo up
unset XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS XDG_DATA_HOME XDG_CONFIG_HOME DOCKER_HOST
export HOME="$W/home" TMPDIR="$W/tmp"
mkdir -p "$HOME" "$TMPDIR" "$W/networks"
cat > "$W/storage.conf" <<EOF
[storage]
driver = "overlay"
graphroot = "$W/store"
runroot = "/run/containers/storage"
[storage.options.overlay]
mount_program = "/usr/bin/fuse-overlayfs"
EOF
cat > "$W/containers.conf" <<EOF
[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
[network]
network_config_dir = "$W/networks"
EOF
export CONTAINERS_STORAGE_CONF="$W/storage.conf" CONTAINERS_CONF="$W/containers.conf"
podman system service --time=0 unix:///run/podman/podman.sock > "$W/podman-service.log" 2>&1 &
SERVICE=$!
for i in $(seq 50); do [ -S /run/podman/podman.sock ] && break; sleep 0.2; done
# Podman schedules health checks with systemd timers, and this host has no systemd: run
# them here instead, every 3 s (a real host's systemd does this; nothing else differs).
( while :; do
    for c in $(podman ps -q 2>/dev/null); do podman healthcheck run "$c" > /dev/null 2>&1; done
    sleep 3
  done ) &
HEALTH=$!
export NGOCR_PREFIX="$W/opt" NGOCR_ENGINE=podman PODMAN_COMPOSE_WARNING_LOGS=false
step() { echo "@@BEGIN $1"; }
done_() { echo "@@STEP $1 $2"; [ "$2" = 0 ] || { teardown; exit 1; }; }
teardown() {
  echo "@@BEGIN teardown"
  if [ -L "$W/opt/current" ]; then
    podman compose -p newgen-ocr -f "$W/opt/current/config/compose.yaml" \
      -f "$W/opt/current/config/compose.images.yaml" --env-file "$W/opt/config/site.env" \
      --profile app --profile ops down -v > /dev/null 2>&1
  fi
  podman rm -af > /dev/null 2>&1
  kill "$HEALTH" "$SERVICE" 2>/dev/null
  echo "@@STEP teardown 0"
}
data() { echo "@@DATA $1 $(tr -d '\n' < "$2")"; }
app() {
  podman compose -p newgen-ocr -f "$W/opt/current/config/compose.yaml" \
    -f "$W/opt/current/config/compose.images.yaml" --env-file "$W/opt/config/site.env" \
    exec -T api python -m "$@"
}
ops_verify() { app newgen_ocr.ops verify > "$W/$1.json" 2>/dev/null; }

step isolation
{ ip -o link | grep -v ': lo:' ; } > "$W/links.txt"
links=$(wc -l < "$W/links.txt")
getent hosts pypi.org > /dev/null 2>&1 && dns=1 || dns=0
images=$(podman images -q | wc -l)
echo "interfaces besides lo: $links; pypi.org resolves: $dns; images in store: $images"
[ "$links" = 0 ] && [ "$dns" = 0 ] && [ "$images" = 0 ]; done_ isolation $?

step outer_signature
ssh-keygen -Y verify -f "$SIGNERS" -I newgen-ocr-release -n newgen-ocr-bundle -s "$A.sig" < "$A"; done_ outer_signature $?

step extract
mkdir -p "$W/media" && tar -xf "$A" -C "$W/media" && ls "$W/media"; done_ extract $?
BA="$W/media/$(basename "$A" .tar)"

step verify
"$BA/ngocrctl" verify --signers "$SIGNERS"; done_ verify $?

step install
start=$(date +%s)
"$BA/ngocrctl" install --signers "$SIGNERS" --self-signed-tls; rc=$?
echo "install took $(( $(date +%s) - start )) s"; done_ install $rc

step restart_no_downloads
"$W/opt/current/ngocrctl" smoke --restart; done_ restart_no_downloads $?

step before_upgrade
ops_verify verify-before && data verify_before "$W/verify-before.json"; done_ before_upgrade $?

step upgrade
ssh-keygen -Y verify -f "$SIGNERS" -I newgen-ocr-release -n newgen-ocr-bundle -s "$B.sig" < "$B" &&
  tar -xf "$B" -C "$W/media" &&
  "$W/media/$(basename "$B" .tar)/ngocrctl" install --signers "$SIGNERS"; done_ upgrade $?

step data_after_upgrade
# New data that only exists after the upgrade: a web user (a row and an audit event).
printf 'rehearsal password after upgrade\n' |
  app newgen_ocr.auth user-create --user after-upgrade --role viewer --password-stdin &&
  app newgen_ocr.auth user-list 2>/dev/null | grep -q '"after-upgrade"'; rc=$?
[ "$rc" = 0 ] && echo "@@DATA upgrade_user_created true"; done_ data_after_upgrade $rc

step rollback
"$W/opt/current/ngocrctl" rollback --restore; done_ rollback $?

step after_rollback
app newgen_ocr.auth user-list 2>/dev/null | grep -q '"after-upgrade"' && gone=false || gone=true
echo "@@DATA upgrade_user_removed $gone"
ops_verify verify-after && data verify_after "$W/verify-after.json" &&
  "$W/opt/current/ngocrctl" status; done_ after_rollback $?

step image_store
podman images --format '{{.Repository}}:{{.Tag}} {{.Id}}' | tee "$W/images.txt"
echo "@@DATA image_refs $(awk '{print $1}' "$W/images.txt" | sort -u | tr '\n' ' ')"
cat "$W/opt/state/history"; done_ image_store 0

teardown
