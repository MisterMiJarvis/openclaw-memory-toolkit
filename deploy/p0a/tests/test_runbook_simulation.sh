#!/usr/bin/env bash
# Test de procédure P0-A — mode SIMULATION, aucune écriture en production.
#
# Démontre :
#   T-A : STOP sur SHA incorrect en préflight
#   T-B : rollback réussi après installation partielle (un seul fichier patché)
#   T-C : patch partiel détecté par la vérification SHA post-installation
#
# Usage : bash test_runbook_simulation.sh
set -uo pipefail

SIM=/home/ubuntu/p0a-sim
PKG=/home/ubuntu/p0a-fix/deploy/p0a
PASS=0; FAIL=0

ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
ko()   { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
head2() { echo; echo "=== $1 ==="; }

rm -rf "$SIM"; mkdir -p "$SIM/skills" "$SIM/backups"

# --- préparation : faux "installés" = copies des originaux de référence
cp "$PKG/../p0a/orig/conflict_resolver.py" "$SIM/skills/" 2>/dev/null || \
  cp /home/ubuntu/cogitator-p0a-patch/orig/conflict_resolver.py "$SIM/skills/"
cp /home/ubuntu/cogitator-p0a-patch/orig/auto_capture.py "$SIM/skills/"
D="$SIM/skills"

EXPECTED_C=4bb9d44c22edb4081464a19024b95e071adf1d2b85b1775fc79cdedb36b3a10a
EXPECTED_A=087493f3b51b9e6df8d38b2a466f0b3218c33a9e327273a4224a1df33e8603f6
AFTER_C=0527f5d490438bde4f7cec83ade8e7a8614de1746850558745d64e9df9df3c21
AFTER_A=5f256b8e30b5c48929852544fb4999b543e37571794508abc5d3920feed91fda

# ─────────────────────────────────────────────────────────────
head2 "T-A : préflight — un SHA altéré DOIT bloquer"

# altere volontairement un fichier (simulation d'une dérive)
echo "# drift" >> "$D/auto_capture.py"

ACT_C=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
ACT_A=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)
BLOCKED=0
if [ "$ACT_C" != "$EXPECTED_C" ] || [ "$ACT_A" != "$EXPECTED_A" ]; then BLOCKED=1; fi
[ "$BLOCKED" -eq 1 ] && ok "préflight bloque sur SHA altéré" || ko "préflight n'a PAS bloqué"

# restaure l'état propre
cp /home/ubuntu/cogitator-p0a-patch/orig/auto_capture.py "$D/auto_capture.py"
ACT_A=$(sha256sum "$D/auto_capture.py" | cut -d' ' -f1)
[ "$ACT_A" = "$EXPECTED_A" ] && ok "SHA restauré conforme" || ko "restauration SHA"

# ─────────────────────────────────────────────────────────────
head2 "T-B : installation PARTIELLE — patch sur un seul fichier"

# sauvegarde (comme §2a)
BK="$SIM/backups/p0a-test"
mkdir -p "$BK"
cp -a "$D/conflict_resolver.py" "$BK/conflict_resolver.py"
cp -a "$D/auto_capture.py"      "$BK/auto_capture.py"
( cd "$BK" && sha256sum conflict_resolver.py auto_capture.py > PY-SHA256.txt )
echo "$BK" > "$SIM/.p0a-backup-path"

# applique UN SEUL des deux patches (simulation d'échec du second)
patch -p0 "$D/conflict_resolver.py" < "$PKG/conflict_resolver.patch" >/dev/null 2>&1
ACT_C=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
ACT_A=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)

PARTIAL=0
if [ "$ACT_C" = "$AFTER_C" ] && [ "$ACT_A" != "$AFTER_A" ]; then PARTIAL=1; fi
[ "$PARTIAL" -eq 1 ] && ok "état partiel détecté (1 patché, 1 non)" || ko "état partiel NON détecté"

# la vérification SHA post-installation doit refuser
GATE=0
if [ "$ACT_C" != "$AFTER_C" ] || [ "$ACT_A" != "$AFTER_A" ]; then GATE=1; fi
[ "$GATE" -eq 1 ] && ok "garde SHA post-install refuse l'installation partielle" || ko "garde SHA inopérant"

# ─────────────────────────────────────────────────────────────
head2 "T-C : rollback NORMAL — restaure les DEUX fichiers"

( cd "$BK" && sha256sum -c PY-SHA256.txt --status ) || ko "manifeste de sauvegarde incohérent"

cp -a "$BK/conflict_resolver.py" "$D/conflict_resolver.py"
cp -a "$BK/auto_capture.py"      "$D/auto_capture.py"

R_C=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
R_A=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)
[ "$R_C" = "$EXPECTED_C" ] && ok "conflict_resolver restauré" || ko "conflict_resolver NON restauré"
[ "$R_A" = "$EXPECTED_A" ] && ok "auto_capture restauré"      || ko "auto_capture NON restauré"

# preuve par le manifeste, comme le fait le vrai script
( cd "$BK" && sha256sum -c PY-SHA256.txt --status ) && ok "manifeste SHA valide après rollback" || ko "manifeste SHA invalide"

# ─────────────────────────────────────────────────────────────
head2 "T-D : aucun placeholder \$BK dans le runbook"

# Les blocs python sont délimités par <<'PY' ... PY (heredoc quoté).
# Dans ces blocs, $BK doit être absent : les chemins passent par sys.argv.
PYPLACEHOLDER=$(awk '/<<.PY.$/{p=1;next} /^PY$/{p=0} p && /\$BK/{c++} END{print c+0}' \
  "$PKG/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null)
[ "$PYPLACEHOLDER" -eq 0 ] && ok "aucun \$BK dans les heredocs python" || ko "$PYPLACEHOLDER placeholder(s) \$BK dans des heredocs python"

# Contrôle complémentaire : chaque bloc python reçoit bien ses chemins par argv
a="$(grep -c "<<'PY'" "$PKG/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null || echo 0)"
b="$(grep -c 'python3 - ' "$PKG/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null || echo 0)"
[ "$a" -eq "$b" ] && ok "tous les heredocs python passent leurs chemins par argv ($a)" \
  || ko "$a heredoc(s) pour $b appel(s) python3 - : un bloc sans argv"

# ─────────────────────────────────────────────────────────────
head2 "T-E : garde-fous bloquants présents"

for pat in 'set -euo pipefail' 'exit 1' 'sha256sum -c' 'cmp -s'; do
  n=$(grep -c "$pat" "$PKG/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null || echo 0)
  [ "$n" -gt 0 ] && ok "présent: $pat ($n)" || ko "absent: $pat"
done

head2 "RÉSULTAT"
echo "  $PASS PASS / $FAIL FAIL"
[ "$FAIL" -eq 0 ] || exit 1
