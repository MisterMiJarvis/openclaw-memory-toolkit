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

# le script réel, invoqué tel quel, avec P0A_D / P0A_BKFILE redirigés
if P0A_D="$D" P0A_BKFILE="$SIM/.p0a-backup-path" \
     bash "$SIM/deploy/p0a/p0a-rollback.sh" >/dev/null 2>&1; then
  ok "rollback réel : PASS"
else
  ko "rollback réel : ÉCHEC"
fi

RC=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
RA=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)
[ "$RC" = "$EXPECTED_C" ] && ok "fichier ACTIF conflict_resolver conforme" || ko "fichier ACTIF conflict_resolver NON conforme"
[ "$RA" = "$EXPECTED_A" ] && ok "fichier ACTIF auto_capture conforme"      || ko "fichier ACTIF auto_capture NON conforme"

# ─────────────────────────────────────────────────────────────
h2 "T-G2 : rollback REFUSE quand les fichiers ACTIFS ne correspondent pas"

# Déterministe, sans concurrence, sans shim, sans sed.
# La sauvegarde contient un fichier CORROMPU mais son manifeste déclare le SHA SAIN.
# Le script copie la sauvegarde vers $D, puis vérifie $D contre le manifeste.
# -> ACT_C != EXPECTED_C -> refus obligatoire.
# L'ancien script (qui vérifiait $BK) aurait répondu PASS : c'est précisément
# la régression que ce test verrouille.

EXPECTED_C=4bb9d44c22edb4081464a19024b95e071adf1d2b85b1775fc79cdedb36b3a10a
EXPECTED_A=087493f3b51b9e6df8d38b2a466f0b3218c33a9e327273a4224a1df33e8603f6

BK_BAD="$SIM/bk-bad"; rm -rf "$BK_BAD"; mkdir -p "$BK_BAD"
cp /home/ubuntu/cogitator-p0a-patch/orig/conflict_resolver.py "$BK_BAD/"
cp /home/ubuntu/cogitator-p0a-patch/orig/auto_capture.py      "$BK_BAD/"
# manifeste : valeurs SAINES attendues
printf '%s  conflict_resolver.py\n%s  auto_capture.py\n' "$EXPECTED_C" "$EXPECTED_A" \
  > "$BK_BAD/PY-SHA256.txt"
# puis on corrompt le fichier sauvegardé APRÈS avoir écrit le manifeste
printf '\n# corrupted after manifest\n' >> "$BK_BAD/conflict_resolver.py"

# vérification préalable : la sauvegarde est bien corrompue, le manifeste bien sain
BK_SHA=$(sha256sum "$BK_BAD/conflict_resolver.py" | cut -d' ' -f1)
[ "$BK_SHA" != "$EXPECTED_C" ] && ok "sauvegarde volontairement corrompue" || ko "sauvegarde NON corrompue — scénario invalide"
grep -q "$EXPECTED_C" "$BK_BAD/PY-SHA256.txt" && ok "manifeste déclare le SHA sain" || ko "manifeste invalide"

D_BAD="$SIM/tg2-install"; rm -rf "$D_BAD"; mkdir -p "$D_BAD"
printf '%s  %s\n' "$EXPECTED_C" conflict_resolver.py > "$SIM/bkpath-bad"
echo "$BK_BAD" > "$SIM/.p0a-tg2-bk"

OUT="$SIM/tg2.out"
P0A_D="$D_BAD" P0A_BKFILE="$SIM/.p0a-tg2-bk" \
  bash "$SIM/deploy/p0a/p0a-rollback.sh" >"$OUT" 2>&1
RC=$?

[ "$RC" -ne 0 ] && ok "rollback sort en code non nul (rc=$RC)" || ko "rollback rc=0 malgré actif non conforme"
grep -q "ROLLBACK NORMAL OK" "$OUT" \
  && ko "message de succès émis malgré actif non conforme" \
  || ok "aucun message de succès émis"
grep -q "conflict_resolver.py actif" "$OUT" \
  && ok "échec attribué au fichier ACTIF" \
  || { ko "échec non attribué au fichier actif"; sed -n '1,5p' "$OUT"; }

# --- cas POSITIF : sauvegarde saine + manifeste sain -> doit réussir
BK_OK="$SIM/bk-ok"; rm -rf "$BK_OK"; mkdir -p "$BK_OK"
cp /home/ubuntu/cogitator-p0a-patch/orig/conflict_resolver.py "$BK_OK/"
cp /home/ubuntu/cogitator-p0a-patch/orig/auto_capture.py      "$BK_OK/"
( cd "$BK_OK" && sha256sum conflict_resolver.py auto_capture.py > PY-SHA256.txt )
echo "$BK_OK" > "$SIM/.p0a-tg2-bk-ok"

D_OK="$SIM/tg2-ok"; rm -rf "$D_OK"; mkdir -p "$D_OK"
echo "# drift avant rollback" > "$D_OK/conflict_resolver.py"

P0A_D="$D_OK" P0A_BKFILE="$SIM/.p0a-tg2-bk-ok" \
  bash "$SIM/deploy/p0a/p0a-rollback.sh" >"$SIM/tg2b.out" 2>&1 \
  && ok "rollback normal réussit quand tout est conforme" \
  || { ko "rollback normal échoue"; sed -n '1,5p' "$SIM/tg2b.out"; }

RC1=$(sha256sum "$D_OK/conflict_resolver.py" | cut -d' ' -f1)
[ "$RC1" = "$EXPECTED_C" ] && ok "actif restauré conforme (cas positif)" || ko "actif NON conforme"

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
