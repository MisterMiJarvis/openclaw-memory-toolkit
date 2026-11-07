#!/usr/bin/env bash
# /home/ubuntu/p0a-rollback.sh
# Rollback NORMAL P0-A : restaure les DEUX fichiers Python et PROUVE que les
# fichiers ACTIFS sont conformes.
# N'effectue AUCUNE restauration SQLite (voir runbook §6b).
set -euo pipefail

D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
BKFILE=/home/ubuntu/.p0a-backup-path

[ -f "$BKFILE" ] || { echo "ROLLBACK IMPOSSIBLE: $BKFILE absent"; exit 1; }
BK=$(cat "$BKFILE")
[ -d "$BK" ] || { echo "ROLLBACK IMPOSSIBLE: $BK absent"; exit 1; }
[ -f "$BK/conflict_resolver.py" ] || { echo "ROLLBACK: conflict_resolver.py introuvable dans $BK"; exit 1; }
[ -f "$BK/auto_capture.py" ]      || { echo "ROLLBACK: auto_capture.py introuvable dans $BK"; exit 1; }
[ -f "$BK/PY-SHA256.txt" ]        || { echo "ROLLBACK: manifeste PY-SHA256.txt absent"; exit 1; }

# SHA ATTENDUS lus dans le manifeste de sauvegarde (état d'origine)
EXPECTED_C=$(grep -E '[[:space:]]conflict_resolver\.py$' "$BK/PY-SHA256.txt" | cut -d' ' -f1)
EXPECTED_A=$(grep -E '[[:space:]]auto_capture\.py$'      "$BK/PY-SHA256.txt" | cut -d' ' -f1)
[ -n "$EXPECTED_C" ] || { echo "ROLLBACK: SHA attendu de conflict_resolver.py illisible"; exit 1; }
[ -n "$EXPECTED_A" ] || { echo "ROLLBACK: SHA attendu de auto_capture.py illisible"; exit 1; }

cp -a "$BK/conflict_resolver.py" "$D/conflict_resolver.py"
cp -a "$BK/auto_capture.py"      "$D/auto_capture.py"

# PREUVE : les fichiers ACTIFS dans $D doivent correspondre aux SHA attendus.
# Ce n'est plus le manifeste relu dans $BK qui décide : ce sont les fichiers vivants.
ACT_C=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
ACT_A=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)

RC=0
if [ "$ACT_C" != "$EXPECTED_C" ]; then
  echo "ROLLBACK ÉCHEC: conflict_resolver.py actif SHA=$ACT_C attendu=$EXPECTED_C"
  RC=1
fi
if [ "$ACT_A" != "$EXPECTED_A" ]; then
  echo "ROLLBACK ÉCHEC: auto_capture.py actif SHA=$ACT_A attendu=$EXPECTED_A"
  RC=1
fi

# Contrôle croisé : le contenu actif doit être bit-à-bit identique à la sauvegarde
cmp -s "$D/conflict_resolver.py" "$BK/conflict_resolver.py" || {
  echo "ROLLBACK ÉCHEC: conflict_resolver.py actif diffère de la sauvegarde"; RC=1; }
cmp -s "$D/auto_capture.py" "$BK/auto_capture.py" || {
  echo "ROLLBACK ÉCHEC: auto_capture.py actif diffère de la sauvegarde"; RC=1; }

[ "$RC" -eq 0 ] || { echo "ROLLBACK NON CONFORME — intervention manuelle requise"; exit 1; }

echo "ROLLBACK NORMAL OK — deux fichiers ACTIFS conformes aux SHA d'origine"
echo "  conflict_resolver.py $ACT_C"
echo "  auto_capture.py      $ACT_A"
