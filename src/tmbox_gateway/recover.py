"""Vägen in när lösenordet är borta.

Servern kräver inloggning även på maskinen själv. Det är rätt: den som sitter
vid tangentbordet ska inte automatiskt vara ägare bara för att hen står i
rummet. Men någonstans måste en glömd inloggning kunna räddas, annars är en
Raspberry Pi i en klubblokal låst för gott av ett bortglömt lösenord.

Beviset är fysisk åtkomst till maskinen, inte en nätverksadress: det här
kommandot körs i serverns terminal och läser dess databas direkt. Den som kan
det kan ändå läsa filen med andra medel - skillnaden är att det nu är en
uttrycklig handling som lämnar en rad i journalen, i stället för en tyst
öppning som gällde varje webbläsare på maskinen.

Kommandot sätter inget lösenord. Det utfärdar en engångskod, samma sort som en
inbjudan, och den som får koden väljer sitt eget lösenord i webbläsaren.

    python -m tmbox_gateway.recover --state-dir /var/lib/trainmeet
    python -m tmbox_gateway.recover --state-dir /var/lib/trainmeet --user casper
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .identity import AdminAccessError, IdentityStore


def _store(state_dir: Path) -> IdentityStore:
    database = state_dir / "identity.db"
    if not database.exists():
        raise SystemExit(
            f"Hittar ingen installation i {state_dir}. Kontrollera sökvägen till serverns datamapp."
        )
    return IdentityStore(database)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Utfärda en engångskod för att sätta ett nytt lösenord på TrainMeet Server",
    )
    parser.add_argument("--state-dir", default="data/local", help="Serverns datamapp")
    parser.add_argument("--user", help="Användarnamn. Utelämnas det listas kontona i stället.")
    arguments = parser.parse_args(argv)

    store = _store(Path(arguments.state_dir))
    try:
        users = store.list_admin_users()
        if not users:
            print("Servern har inga konton än. Öppna webbgränssnittet och gör installationen.")
            return 1

        if not arguments.user:
            print("Konton på den här servern:\n")
            for user in users:
                role = "ägare" if user["role"] == "owner" else "administratör"
                state = "inbjuden" if user["invitation_pending"] else "aktiv"
                print(f"  {user['username']}  ({role}, {state})")
            print("\nKör igen med --user <användarnamn> för att få en engångskod.")
            return 0

        match = next(
            (user for user in users if str(user["username"]).lower() == arguments.user.lower()),
            None,
        )
        if match is None:
            print(f"Ingen användare heter {arguments.user}.", file=sys.stderr)
            return 1

        try:
            issued = store.reissue_admin_setup(str(match["user_id"]))
        except AdminAccessError as error:
            print(str(error), file=sys.stderr)
            return 1

        print(f"\n  Kod till {issued['username']}:  {issued['setup_code']}\n")
        print("Öppna TrainMeet Server i en webbläsare, välj \"Jag har en inbjudningskod\"")
        print("och sätt ett nytt lösenord. Koden gäller i sju dagar och bara en gång.")
        print("Det gamla lösenordet slutar gälla när koden löses in.")
        return 0
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover - körs från terminalen
    raise SystemExit(main())
