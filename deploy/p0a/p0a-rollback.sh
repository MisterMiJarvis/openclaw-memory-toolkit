#!/usr/bin/env bash
# /home/ubuntu/p0a-rollback.sh
# Rollback NORMAL P0-A : restaure les DEUX fichiers Python.
# N'effectue AUCUNE restauration SQLite (voir runbook §6b).
set -euo pipefail

D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
BKFILE=/home/ubuntu/.p0a-backup-path

[ -f "$BKFILE" ] || { echo "ROLLBACK IMPOSSIBLE: $BKFILE absent"; exit 1; }
BK=$(cat "$BKFILE")
[ -d "$BK" ] || { echo "ROLLBACK IMPOSSIBLE: $BK absent"; exit 1; }
[ -f "$BK/conflict_resolver.py" ] || { echo "ROLLBACK: conflict_resolver.py introuvable dans $BK"; exit 1; }
[ -f "$BK/auto_capture.py" ]      || { echo "ROLLBACK: auto_capture.py introuvable dans $BK"; exit 1; }

cp -a "$BK/conflict_resolver.py" "$D/conflict_resolver.py"
cp -a "$BK/auto_capture.py"      "$D/auto_capture.py"

# preuve : les SHA restaurés doivent être identiques aux originaux
( cd "$BK" && sha256sum -c PY-SHA256.txt --status ) \
  || { echo "ROLLBACK: SHA restaurés NON conformes"; exit 1; }

echo "ROLLBACK NORMAL OK — deux fichiers restaurés depuis $BK"
