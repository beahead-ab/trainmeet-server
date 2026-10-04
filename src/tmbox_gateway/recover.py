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

Kontot är e-postadressen. Ett konto som saknar adress, till exempel ett från
före version 3, kan inte logga in förrän det har fått en. Här ger man den med
--email, samtidigt som koden utfärdas.

    python -m tmbox_gateway.recover --state-dir /var/lib/trainmeet-server
    python -m tmbox_gateway.recover --state-dir /var/lib/trainmeet-server --konto casper@example.se
    python -m tmbox_gateway.recover --state-dir /var/lib/trainmeet-server --konto 1 --email casper@example.se
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .identity import AdminAccessError, IdentityStore
from .local_server import _database_path


def _store(state_dir: Path) -> IdentityStore:
    # Samma fil som servern läser kontona ur. Kommandot letade förut efter en
    # identity.db som ingen installation har, och hittade då ingenting.
    database = _database_path(state_dir)
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
    parser.add_argument(
        "--konto",
        help="Kontots nummer i listan eller dess e-postadress. Utelämnas det listas kontona i stället.",
    )
    parser.add_argument(
        "--email",
        help="Ny e-postadress för kontot. Krävs när kontot saknar adress, eftersom adressen är det man loggar in med.",
    )
    arguments = parser.parse_args(argv)

    store = _store(Path(arguments.state_dir))
    try:
        users = store.list_admin_users()
        if not users:
            print("Servern har inga konton än. Öppna webbgränssnittet och gör installationen.")
            return 1

        if not arguments.konto:
            print("Konton på den här servern:\n")
            for number, user in enumerate(users, start=1):
                role = "ägare" if user["role"] == "owner" else "administratör"
                state = "inbjuden" if user["invitation_pending"] else "aktiv"
                email = user["email"] or "saknar e-post, kan inte logga in"
                print(f"  {number}. {user['display_name']}  <{email}>  ({role}, {state})")
            print("\nKör igen med --konto <nummer eller e-post> för att få en engångskod.")
            print("Saknar kontot e-post anger du en med --email <adress>.")
            return 0

        wanted = arguments.konto.strip()
        if wanted.isdigit():
            index = int(wanted) - 1
            match = users[index] if 0 <= index < len(users) else None
        else:
            match = next((user for user in users if user["email"] and user["email"] == wanted.lower()), None)
        if match is None:
            print(f"Hittar inget konto {wanted}. Kör utan --konto för att se listan.", file=sys.stderr)
            return 1

        try:
            if arguments.email:
                store.set_admin_user_email(str(match["user_id"]), arguments.email)
            elif not match["email"]:
                print(
                    f"{match['display_name']} saknar e-postadress och kan inte logga in utan en."
                    " Kör igen med --email <adress>.",
                    file=sys.stderr,
                )
                return 1
            issued = store.reissue_admin_setup(str(match["user_id"]))
        except AdminAccessError as error:
            print(str(error), file=sys.stderr)
            return 1

        print(f"\n  Kod till {issued['display_name']} <{issued['email']}>:  {issued['setup_code']}\n")
        print("Öppna TrainMeet Server i en webbläsare, välj \"Jag har en kod\" och ange")
        print("e-postadressen, koden och ett nytt lösenord. Koden gäller i sju dagar och")
        print("bara en gång. Det gamla lösenordet slutar gälla när koden löses in.")
        return 0
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover - körs från terminalen
    raise SystemExit(main())
