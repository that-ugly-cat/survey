"""
Link existing researchers to the subjects an SSO gate knows them by.

Run once, by hand, BEFORE switching AUTH_MODE to `gateway`:

    docker exec survey python map_borant.py --map you@example.org=01ABC...
    docker exec survey python map_borant.py --report

Linking by email at request time would be the obvious shortcut, and it is the
one to avoid: one typo in the gate's admin panel would silently hand somebody
another researcher's surveys — and with them the responses of people who
answered a questionnaire on the understanding that a named research team would
read them. A script gets read before it runs and prints what it did.

Respondents are not in this table and never will be: a questionnaire is answered
without an account, and nothing here changes that.
"""
import argparse
import os
import sqlite3
import sys

DB_PATH = os.getenv("DB_PATH", "/data/survey.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", action="append", default=[], metavar="EMAIL=SUBJECT")
    ap.add_argument("--unlink", action="append", default=[], metavar="EMAIL")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    changed = 0

    for pair in args.map:
        email, sep, subject = pair.partition("=")
        email, subject = email.strip().lower(), subject.strip()
        if not sep or not email or not subject:
            print(f"  SALTO     {pair!r}: serve la forma email=subject"); continue
        u = db.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if u is None:
            print(f"  ASSENTE   {email}: nessun utente con questo indirizzo"); continue
        if u["borant_sub"] == subject:
            print(f"  GIA-OK    {email} -> {subject}"); continue
        if u["borant_sub"]:
            print(f"  CONFLITTO {email}: gia' legato a {u['borant_sub']}, non sovrascrivo. "
                  f"Usa --unlink prima, se e' voluto."); continue
        clash = db.execute("SELECT email FROM users WHERE borant_sub = ?", (subject,)).fetchone()
        if clash:
            print(f"  CONFLITTO {email}: il subject {subject} e' gia' di {clash['email']}"); continue
        db.execute("UPDATE users SET borant_sub = ? WHERE id = ?", (subject, u["id"]))
        changed += 1
        n = db.execute("SELECT COUNT(*) FROM surveys WHERE owner_id = ?", (u["id"],)).fetchone()[0]
        print(f"  LEGATO    {email} -> {subject}  ({n} questionari)")

    for email in args.unlink:
        email = email.strip().lower()
        u = db.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if u is None or not u["borant_sub"]:
            print(f"  NIENTE    {email}: non era legato"); continue
        print(f"  SLEGATO   {email} (era {u['borant_sub']})")
        db.execute("UPDATE users SET borant_sub = NULL WHERE id = ?", (u["id"],))
        changed += 1

    if changed:
        db.commit()

    print("\n-- stato dei ricercatori --")
    scoperti = []
    for u in db.execute("SELECT * FROM users ORDER BY id"):
        n = db.execute("SELECT COUNT(*) FROM surveys WHERE owner_id = ?", (u["id"],)).fetchone()[0]
        r = db.execute("SELECT COUNT(*) FROM responses x JOIN surveys s ON s.id = x.survey_id "
                       "WHERE s.owner_id = ?", (u["id"],)).fetchone()[0]
        flag = " ADMIN" if u["is_admin"] else ""
        print(f"  {u['email']:<34} {u['borant_sub'] or '(nessun legame)':<28} "
              f"questionari={n} risposte={r}{flag}")
        if not u["borant_sub"] and u["is_active"]:
            scoperti.append((u, n))

    print(f"\n  {len(scoperti)} attivi senza legame.")
    if scoperti:
        print("  In `gateway` arrivano come profilo NUOVO, quindi senza i loro")
        print("  questionari e senza le risposte raccolte. Legali prima di accendere.")
        persi = sum(n for _, n in scoperti)
        if persi:
            print(f"  Fra loro ci sono {persi} questionari che resterebbero attaccati a")
            print("  profili irraggiungibili dal gate.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
