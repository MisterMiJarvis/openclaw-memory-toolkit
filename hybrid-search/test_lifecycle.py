import sys
sys.path.insert(0,'/home/ubuntu/cogitator-audit/openclaw-memory-toolkit/hybrid-search')
from lifecycle import infer_state, can_transition, assert_not_inferred_execution

CASES = [
 # (texte, état attendu)
 ("Je prévois de tester le nouveau protocole reader la semaine prochaine.", "envisaged"),
 ("Il faudrait peut-être migrer les embeddings un jour.", "envisaged"),
 ("J'ai décidé de garder PostgreSQL en local.", "decided"),
 ("J'ai commencé la migration de la base vers PostgreSQL 17.", "started"),
 ("La tâche de nettoyage des logs est bloquée depuis deux jours.", "blocked"),
 ("J'ai terminé la migration, c'est fait.", "done"),
 ("Le scan de sécurité mensuel est terminé, il a rendu un rapport propre.", "done"),
 ("Finalement on annule le renouvellement du certificat.", "envisaged"),
]

ok=fail=0
print("=== infer_state ===")
for text,exp in CASES:
    got=infer_state(text)
    good = got==exp
    ok+=good; fail+= (not good)
    print(f"  {'PASS' if good else 'FAIL'} attendu={exp:10} obtenu={got:10} | {text[:55]}")

print("\n=== transitions ===")
TRANS=[("envisaged","decided",True),("envisaged","done",False),
       ("decided","started",True),("decided","done",False),
       ("started","done",True),("started","blocked",True),
       ("done","started",False),("cancelled","decided",False),
       ("envisaged","cancelled",True)]
for a,b,exp in TRANS:
    got,why=can_transition(a,b)
    good=got==exp
    ok+=good; fail+=(not good)
    print(f"  {'PASS' if good else 'FAIL'} {a:10}->{b:10} attendu={exp} obtenu={got} ({why})")

print("\n=== refus d'exécution inférée ===")
REF=[
 ("Je prévois de tester le protocole la semaine prochaine.","done",False),
 ("Il faudrait migrer un jour.","done",False),
 ("J'ai terminé la migration.","done",True),
]
for text,state,exp_ok in REF:
    try:
        assert_not_inferred_execution(text,state); got=True
    except ValueError as e:
        got=False
    good=got==exp_ok
    ok+=good; fail+=(not good)
    print(f"  {'PASS' if good else 'FAIL'} accepté={got} attendu={exp_ok} | {text[:45]}")

print(f"\nRÉSULTAT: {ok} pass, {fail} fail")
sys.exit(1 if fail else 0)
