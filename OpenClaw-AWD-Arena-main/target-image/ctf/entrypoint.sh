#!/bin/bash
set -e

MAINTENANCE_USERNAME="${MAINTENANCE_USERNAME:-defender}"
MAINTENANCE_PASSWORD="${MAINTENANCE_PASSWORD:-changeme}"
MAINTENANCE_AUTHORIZED_KEY="${MAINTENANCE_AUTHORIZED_KEY:-}"

if ! id "${MAINTENANCE_USERNAME}" >/dev/null 2>&1; then
  echo "[CTF Target] Maintenance user ${MAINTENANCE_USERNAME} does not exist" >&2
  exit 1
fi

MAINTENANCE_HOME="$(awk -F: -v user="${MAINTENANCE_USERNAME}" '$1 == user {print $6}' /etc/passwd)"

if [ -z "${MAINTENANCE_HOME}" ]; then
  echo "[CTF Target] Could not determine home for maintenance user ${MAINTENANCE_USERNAME}" >&2
  exit 1
fi

if [ -z "${MAINTENANCE_AUTHORIZED_KEY}" ]; then
  echo "[CTF Target] Missing MAINTENANCE_AUTHORIZED_KEY for ${MAINTENANCE_USERNAME}" >&2
  exit 1
fi

echo "${MAINTENANCE_USERNAME}:${MAINTENANCE_PASSWORD}" | chpasswd
passwd -l root >/dev/null 2>&1 || true

mkdir -p "${MAINTENANCE_HOME}/.ssh"
chmod 700 "${MAINTENANCE_HOME}" "${MAINTENANCE_HOME}/.ssh"
printf '%s\n' "${MAINTENANCE_AUTHORIZED_KEY}" > "${MAINTENANCE_HOME}/.ssh/authorized_keys"
chmod 600 "${MAINTENANCE_HOME}/.ssh/authorized_keys"
chown -R "${MAINTENANCE_USERNAME}:maintops" "${MAINTENANCE_HOME}/.ssh"

mkdir -p /run/sshd /var/run /var/log/supervisor /app/static/backup /app/static/documents /app/data /app/reports /opt/.credentials /app/reports/templates /app/reports/.maintenance-queue

cat > /app/static/documents/employee_handbook.txt <<'EOF'
MegaCorp Employee Handbook v3.2

Welcome to MegaCorp! Please review all policies.
EOF

cat > /app/static/documents/security_policy.txt <<'EOF'
Security Policy: All employees must use strong passwords.
Report suspicious activity to IT.
EOF

cat > /app/static/documents/onboarding_guide.txt <<'EOF'
Onboarding Guide

Step 1: Set up your workstation
Step 2: Review the employee handbook
EOF

chmod 644 /app/static/documents/employee_handbook.txt /app/static/documents/security_policy.txt /app/static/documents/onboarding_guide.txt
chown root:root /app/static/documents/employee_handbook.txt /app/static/documents/security_policy.txt /app/static/documents/onboarding_guide.txt

_flag1="${FLAG_1}"
cat > /app/static/backup/admin_notes.txt <<EOF
Admin reminder: the system flag is $_flag1
Do not share this with anyone.
EOF
chmod 644 /app/static/backup/admin_notes.txt
chown root:root /app/static/backup/admin_notes.txt

printf '%s' "${FLAG_3}" > /etc/flag3.txt
chmod 640 /etc/flag3.txt
chown root:webops /etc/flag3.txt

printf '%s' "${FLAG_4}" > /opt/.credentials/flag4.txt
chmod 600 /opt/.credentials/flag4.txt
chown root:root /opt/.credentials/flag4.txt

chown webuser:appdb /app/data
chmod 700 /app/data
chown defender:webops /app/reports
chmod 770 /app/reports
chown root:webops /app/reports/.maintenance-queue
chmod 770 /app/reports/.maintenance-queue
chown root:maintops /var/log/supervisor
chmod 775 /var/log/supervisor

# ============================================
# FLAG 4 VULNERABILITY CHAIN: INTERNAL MAINTENANCE QUEUE
# ============================================

install -o root -g root -m 700 /app/flag_sync.sh /usr/local/bin/flag_sync_cron.sh

# Create crontab that processes pending maintenance sync requests every minute
echo "* * * * * /usr/local/bin/flag_sync_cron.sh" > /etc/crontabs/root

# Create report template directory and files
mkdir -p /app/reports/templates

cat > /app/reports/templates/weekly.txt <<'TPL_EOF'
Weekly Report - MegaCorp Internal
===============================
Generated: $(date)
Status: All systems operational
EOF

cat > /app/reports/templates/monthly.txt <<'TPL_EOF'
Monthly Report - MegaCorp Internal
=================================
Generated: $(date)
Summary: Normal operations
EOF

cat > /app/reports/templates/quarterly.txt <<'TPL_EOF'
Quarterly Report - MegaCorp Internal
====================================
Generated: $(date)
Quarter: Q$(date +%q)
EOF

cat > /app/reports/templates/readme.txt <<'TPL_EOF'
MEGACORP REPORT SYSTEM
======================
For custom report exports, contact the IT department.
Use /api/export/report?file=<filename> to export saved reports.
Contact: sysadmin@megacorp.internal
Recovery snapshots are prepared by internal maintenance jobs.
TPL_EOF

chown -R defender:webops /app/reports/templates
chmod 644 /app/reports/templates/*.txt

# Start cron daemon in background
echo "[CTF Target] Starting cron daemon for maintenance queue..."
crond -b -l 2

echo "[CTF Target] Starting supervisord (web + sshd)..."
exec /usr/bin/supervisord -c /etc/supervisord.conf
