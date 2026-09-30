"""Hur gatewayerna håller sin anslutning till mäklaren.

Båda gatewayerna anslöt med tio sekunders keepalive och en ny session varje
gång. På en Raspberry Pi under last är tio sekunder för stramt: mäklaren
kopplar bort en klient som inte hörts på ungefär femton sekunder, och ett enda
segt varv i serverns loop räcker.

Priset betalas inte av servern utan av rummet. Gatewayen har ett kvarhållet
testamente - `status: offline` - så i samma stund mäklaren släpper den ser
*varje* TMBox att servern är borta. Alla displayer slår om samtidigt, och
eftersom sessionen dessutom startades om från noll måste allt prenumereras och
skickas igen innan boxarna kan komma tillbaka. Uppmätt på en träff: elva
sekunders drift, två sekunder SERVER SAKNAS, tolv sekunder ANSLUTER SERVER, om
och om igen.

Två inställningar, ett ställe: marginalen ska vara bred nog för hårdvaran vi
kör på, och en blink ska inte kosta ett helt omtag.
"""

from __future__ import annotations

from typing import Any

#: Mäklaren släpper en klient efter 1,5 × keepalive. Sextio sekunder ger nittio
#: sekunders marginal, vilket är mer än någon rimlig hackning på en Pi - och
#: fortfarande snabbt nog att märka en server som faktiskt försvunnit.
KEEPALIVE_SECONDS = 60

#: Hur länge mäklaren håller sessionen vid liv utan klienten. Över en kort
#: frånvaro överlever prenumerationerna, så återkomsten kostar en anslutning i
#: stället för ett helt omtag. Fem minuter räcker för omstarter och blinkningar
#: utan att köa meddelanden i evighet åt en server som är borta på riktigt.
SESSION_EXPIRY_SECONDS = 300


def connect(client: Any, host: str, port: int) -> None:
    """Anslut med gemensam marginal och en session som överlever en blink."""

    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties

    properties = Properties(PacketTypes.CONNECT)
    properties.SessionExpiryInterval = SESSION_EXPIRY_SECONDS
    client.connect(
        host,
        port,
        keepalive=KEEPALIVE_SECONDS,
        # clean_start=False utan SessionExpiryInterval vore verkningslöst:
        # mäklaren kastar sessionen i samma stund anslutningen bryts.
        clean_start=False,
        properties=properties,
    )
