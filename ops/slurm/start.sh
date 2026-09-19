#!/usr/bin/env bash
set -euo pipefail
if [[ $(hostname) != reprohpc-lab ]]; then
  echo 'This bootstrap is only for the disposable reprohpc-lab container.' >&2
  exit 2
fi
python /workspace/ops/slurm/init_cgroups.py
install -m 644 /workspace/ops/slurm/slurm.conf /etc/slurm/slurm.conf
install -m 644 /workspace/ops/slurm/cgroup.conf /etc/slurm/cgroup.conf
mkdir -p /run/munge /run/mysqld /var/log/slurm
chown munge:munge /run/munge
chown mysql:mysql /run/mysqld
chown slurm:slurm /var/log/slurm /var/spool/slurmctld
if [[ ! -s /etc/munge/munge.key ]]; then
  dd if=/dev/urandom of=/etc/munge/munge.key bs=1024 count=1 status=none
fi
chown munge:munge /etc/munge/munge.key
chmod 400 /etc/munge/munge.key
runuser -u munge -- munged
mariadbd --user=mysql --skip-networking --socket=/run/mysqld/mysqld.sock &
for attempt in $(seq 1 30); do
  if mariadb-admin ping --silent; then break; fi
  sleep 1
done
# Lab-only socket-local database account. No service is exposed on the host.
mariadb <<'SQL'
CREATE DATABASE IF NOT EXISTS slurm_acct_db;
CREATE USER IF NOT EXISTS 'slurm'@'localhost' IDENTIFIED BY 'reprohpc-disposable-lab';
GRANT ALL ON slurm_acct_db.* TO 'slurm'@'localhost';
FLUSH PRIVILEGES;
SQL
cat >/etc/slurm/slurmdbd.conf <<'CONFIG'
AuthType=auth/munge
DbdHost=localhost
DbdPort=6819
SlurmUser=slurm
StorageType=accounting_storage/mysql
StorageHost=localhost
StorageUser=slurm
StoragePass=reprohpc-disposable-lab
StorageLoc=slurm_acct_db
LogFile=/var/log/slurm/slurmdbd.log
PidFile=/run/slurmdbd.pid
CONFIG
chown slurm:slurm /etc/slurm/slurmdbd.conf
chmod 600 /etc/slurm/slurmdbd.conf
slurmdbd
for attempt in $(seq 1 30); do
  if sacctmgr -n show cluster >/dev/null 2>&1; then break; fi
  sleep 1
done
# The accounting database survives a container restart; register each entity only once.
registered() { [[ -n $(sacctmgr -n -P show "$1" "$2" format="$3") ]]; }
registered cluster reprohpc cluster || sacctmgr -i add cluster reprohpc
registered account research account || sacctmgr -i add account research Description=ReproHPC Organization=ReproHPC
registered user researcher user || sacctmgr -i add user researcher Account=research
for limit in 1 2 4; do
  registered qos "bench$limit" name || sacctmgr -i add qos "bench$limit" MaxJobsPU="$limit"
done
sacctmgr -i modify user researcher set qos=normal,bench1,bench2,bench4 || true
slurmctld
slurmd -N worker
scontrol update NodeName=worker State=RESUME || true
sinfo -Nel
