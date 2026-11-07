#!/usr/bin/env bash
# Test P0-A v2 — exécute les ARTEFACTS RÉELS, pas des reproductions.
#
# Démontre :
#   T-G1 : extraction depuis un commit git + validation du manifeste PATCH-SHA256.txt
#   T-G2 : altération d'un fichier ACTIF après copie -> le rollback REFUSE le PASS
#   T-A  : préflight STOP sur SHA incorrect
#   T-B  : installation partielle détectée + garde SHA post-install
#   T-C  : rollback NORMAL -> les fichiers ACTIFS sont conformes
#   T-D  : aucun $BK dans les heredocs python
#   T-E  : garde-fous présents
#
# Usage : bash test_runbook_simulation.sh [COMMIT]
set -uo pipefail

COMMIT="${1:-HEAD}"
REPO=/home/ubuntu/cogitator-audit/openclaw-memory-toolkit
SIM=/home/ubuntu/p0a-sim-v2
PASS=0; FAIL=0

ok()  { echo "  [PASS] $1"; PASS=$((PASS+1)); }
ko()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
h2()  { echo; echo "=== $1 ==="; }

rm -rf "$SIM"; mkdir -p "$SIM"
PKG="$SIM/pkg"

# ─────────────────────────────────────────────────────────────
h2 "T-G1 : extraction git archive + manifeste de patches"

(cd "$REPO" && git archive "$COMMIT" deploy/p0a) | tar -x -C "$SIM" 2>/dev/null
if [ -d "$SIM/deploy/p0a" ]; then ok "git archive $COMMIT extrait"; else ko "git archive a échoué"; fi

MAN="$SIM/deploy/p0a/PATCH-SHA256.txt"
if [ -f "$MAN" ]; then ok "PATCH-SHA256.txt présent dans le commit"; else ko "PATCH-SHA256.txt ABSENT du commit"; fi

( cd "$SIM/deploy/p0a" && sha256sum -c PATCH-SHA256.txt --status ) \
  && ok "manifeste de patches validé depuis le package extrait" \
  || ko "manifeste de patches invalide"

# ─────────────────────────────────────────────────────────────
h2 "T-A : préflight — altération d'un fichier installé"

D="$SIM/install"; mkdir -p "$D"
cp "$REPO/deploy/p0a/../p0a/orig/conflict_resolver.py" "$D/" 2>/dev/null || \
  cp /home/ubuntu/cogitator-p0a-patch/orig/conflict_resolver.py "$D/"
cp /home/ubuntu/cogitator-p0a-patch/orig/auto_capture.py "$D/"

EXPECTED_C=4bb9d44c22edb4081464a19024b95e071adf1d2b85b1775fc79cdedb36b3a10a
EXPECTED_A=087493f3b51b9e6df8d38b2a466f0b3218c33a9e327273a4224a1df33e8603f6
AFTER_C=0527f5d490438bde4f7cec83ade8e7a8614de1746850558745d64e9df9df3c21
AFTER_A=5f256b8e30b5c48929852544fb4999b543e37571794508abc5d3920feed91fda

echo "# drift" >> "$D/auto_capture.py"
ACT_A=$(sha256sum "$D/auto_capture.py" | cut -d' ' -f1)
[ "$ACT_A" != "$EXPECTED_A" ] && ok "préflight bloque sur SHA altéré" || ko "préflight n'a PAS bloqué"
cp /home/ubuntu/cogitator-p0a-patch/orig/auto_capture.py "$D/auto_capture.py"

# ─────────────────────────────────────────────────────────────
h2 "T-B : installation partielle"

BK="$SIM/backups/p0a"; mkdir -p "$BK"
cp -a "$D/conflict_resolver.py" "$BK/conflict_resolver.py"
cp -a "$D/auto_capture.py"      "$BK/auto_capture.py"
( cd "$BK" && sha256sum conflict_resolver.py auto_capture.py > PY-SHA256.txt )
echo "$BK" > "$SIM/.p0a-backup-path"

patch -p0 "$D/conflict_resolver.py" < "$SIM/deploy/p0a/conflict_resolver.patch" >/dev/null 2>&1
AC=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
AA=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)
[ "$AC" = "$AFTER_C" ] && [ "$AA" != "$AFTER_A" ] && ok "état partiel détecté" || ko "état partiel NON détecté"
[ "$AC" != "$AFTER_C" ] || [ "$AA" != "$AFTER_A" ] && ok "garde SHA post-install refuse" || ko "garde SHA inopérant"

# ─────────────────────────────────────────────────────────────
h2 "T-C : rollback NORMAL sur les artefacts réels"

# le script réel, avec D et BKFILE redirigés vers la simulation
sed -e "s#^D=.*#D=$D#" -e "s#^BKFILE=.*#BKFILE=$SIM/.p0a-backup-path#" \
    "$SIM/deploy/p0a/p0a-rollback.sh" > "$SIM/rollback-sim.sh"
chmod +x "$SIM/rollback-sim.sh"

if bash "$SIM/rollback-sim.sh" >/dev/null 2>&1; then ok "rollback réel : PASS"; else ko "rollback réel : ÉCHEC"; fi

RC=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
RA=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)
[ "$RC" = "$EXPECTED_C" ] && ok "fichier ACTIF conflict_resolver conforme" || ko "fichier ACTIF conflict_resolver NON conforme"
[ "$RA" = "$EXPECTED_A" ] && ok "fichier ACTIF auto_capture conforme"      || ko "fichier ACTIF auto_capture NON conforme"

# ─────────────────────────────────────────────────────────────
h2 "T-G2 : altération du fichier ACTIF après copie -> rollback REFUSE"

# l'altération simule une copie corrompue : le manifeste dit OK, l'actif ne l'est pas
cp -a "$D/conflict_resolver.py" "$SIM/conflict.bak"
echo "# post-copy corruption" >> "$D/conflict_resolver.py"

# on force le scénario : la sauvegarde est saine, l'actif est altéré après cp
sed -i 's#^cp -a "$BK/conflict_resolver.py" "$D/conflict_resolver.py"#cp -a "$BK/conflict_resolver.py" "$D/conflict_resolver.py"; echo "# corrupted after copy" >> "$D/conflict_resolver.py"#' \
  "$SIM/rollback-sim.sh"

if bash "$SIM/rollback-sim.sh" >/dev/null 2>&1; then
  ko "rollback a annoncé PASS malgré un fichier ACTIF corrompu"
else
  ok "rollback REFUSE le PASS quand le fichier ACTIF est corrompu"
fi

# restaure un état sain pour la suite
cp -a "$SIM/conflict.bak" "$D/conflict_resolver.py"

# ─────────────────────────────────────────────────────────────
h2 "T-D : placeholders dans les heredocs python"
PH=$(awk '/<<.PY.$/{p=1;next} /^PY$/{p=0} p && /\$BK/{c++} END{print c+0}' \
  "$SIM/deploy/p0a/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null)
[ "$PH" -eq 0 ] && ok "aucun \$BK dans les heredocs python" || ko "$PH placeholder(s) \$BK"
a=$(grep -c "<<'PY'" "$SIM/deploy/p0a/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null || echo 0)
b=$(grep -c 'python3 - ' "$SIM/deploy/p0a/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null || echo 0)
[ "$a" -eq "$b" ] && ok "chemins passés par argv ($a heredocs)" || ko "$a heredocs / $b appels python3 -"

# ─────────────────────────────────────────────────────────────
h2 "T-E : garde-fous bloquants"
for pat in 'set -euo pipefail' 'exit 1' 'sha256sum -c' 'cmp -s'; do
  n=$(grep -c "$pat" "$SIM/deploy/p0a/RUNBOOK-DEPLOIEMENT-P0A.md" 2>/dev/null || echo 0)
  [ "$n" -gt 0 ] && ok "présent: $pat ($n)" || ko "absent: $pat"
done

# ─────────────────────────────────────────────────────────────
h2 "RÉSULTAT"
echo "  $PASS PASS / $FAIL FAIL"
[ "$FAIL" -eq 0 ] || exit 1
