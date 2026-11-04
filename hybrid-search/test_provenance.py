import sys
sys.path.insert(0,'/home/ubuntu/cogitator-audit/openclaw-memory-toolkit/hybrid-search')
import provenance as P

CASES = [
 ("OK",           "session=p1-sess-A message=p1-A-001"),
 ("NOT_FOUND",    "session=p1-sess-A message=does-not-exist-xyz"),
 ("MALFORMED",    "le serveur tourne sous Ubuntu"),
 ("MALFORMED",    None),
 ("WRONG_SESSION","session=autre-session message=p1-A-001"),
]
ok=fail=0
for exp,sc in CASES:
    r=P.resolve(sc)
    good = r["status"]==exp
    ok+=good; fail+=(not good)
    print(f"{'PASS' if good else 'FAIL'} attendu={exp:13} obtenu={r['status']:13} | {str(sc)[:45]}")
    if r["status"]=="OK":
        print(f"      role={r['role']} ts={r['timestamp']} excerpt={r['excerpt'][:60]!r}")
print(f"\n{ok} pass, {fail} fail")
sys.exit(1 if fail else 0)
