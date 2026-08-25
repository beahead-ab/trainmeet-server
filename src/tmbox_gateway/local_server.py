from __future__ import annotations

import argparse
import logging
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

from . import backup
from .central_sync import DEFAULT_RUNTIME_PUBLICATION_URL
from .engine import TrafficEngine
from .http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from .identity import DeviceKind, IdentityStore, PairingService
from .local_config import SQLiteLocalConfigurationStore
from .models import unconfigured_session
from .observability import configure_logging
from .mqtt_adapter import MQTTGatewayAdapter
from .mqtt_v2 import MQTTV2Adapter, TMBoxV2Gateway
from .operations import SQLiteOperationsStore
from .protocol_v2 import TMBoxStationService
from .runtime import RuntimePublicationError, SQLiteRuntimeStore
from .software_update import supports_updates
from .storage import SQLiteStateStore


LOGGER = logging.getLogger("tmbox_gateway.server")


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TrainMeet Server – lokal trafikserver för Mac och Raspberry Pi"
    )
    parser.add_argument("--bind", default="0.0.0.0", help="Adress för lokal webb och MQTT")
    parser.add_argument("--http-port", type=int, default=8787)
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument(
        "--mqtt-host",
        default=os.environ.get("TRAINMEET_MQTT_HOST", "127.0.0.1"),
        help="Adress till extern MQTT-broker (standard: localhost)",
    )
    parser.add_argument(
        "--advertised-host",
        default=os.environ.get("TRAINMEET_ADVERTISED_HOST", ""),
        help=(
            "IP eller domän som visas för TMBox-anslutning. Krävs i Docker/"
            "Kubernetes: containern ser bara sitt eget interna nätverk, inte "
            "värdens riktiga adress. Behövs inte vid installation direkt på "
            "Pi, Mac eller en Linuxserver."
        ),
    )
    parser.add_argument(
        "--broker-wait-seconds",
        type=float,
        default=15,
        help="Hur länge servern väntar på en extern MQTT-broker",
    )
    parser.add_argument("--gateway-id", default=socket.gethostname().split(".")[0])
    parser.add_argument("--state-dir", default="data/local")
    parser.add_argument("--pairing-code", help=argparse.SUPPRESS)
    parser.add_argument(
        "--central-url",
        default=os.environ.get("TRAINMEET_RUNTIME_URL", DEFAULT_RUNTIME_PUBLICATION_URL),
        help="TrainMeet endpoint used when an admin enters a six-digit sync code",
    )
    parser.add_argument(
        "--external-broker",
        action="store_true",
        help="Use an already running Mosquitto service instead of starting one",
    )
    parser.add_argument(
        "--force-external-auth",
        action="store_true",
        default=os.environ.get("TRAINMEET_FORCE_EXTERNAL_AUTH", "").lower() in {"1", "true", "yes"},
        help="Require admin login for every web request (use behind a proxy or Kubernetes ingress)",
    )
    args = parser.parse_args()

    configure_logging(logging.INFO)
    state_directory = Path(args.state_dir).resolve()
    state_directory.mkdir(parents=True, exist_ok=True)

    broker: subprocess.Popen[bytes] | None = None
    if args.external_broker:
        broker_host = args.mqtt_host
        if not _wait_for_port(broker_host, args.mqtt_port, args.broker_wait_seconds):
            raise SystemExit(
                f"Den externa MQTT-tjänsten på {broker_host}:{args.mqtt_port} är inte startad"
            )
    else:
        broker = _start_broker(args.bind, args.mqtt_port, state_directory)
        broker_host = "127.0.0.1"
    database_path = _database_path(state_directory)
    runtime_store = SQLiteRuntimeStore(database_path)
    operations_store = SQLiteOperationsStore(database_path)
    local_configuration_store = SQLiteLocalConfigurationStore(database_path)
    try:
        active_publication = runtime_store.active()
    except RuntimePublicationError as error:
        LOGGER.error("Den aktiva träffkonfigurationen kunde inte startas: %s", error)
        runtime_store.quarantine_active(str(error))
        active_publication = None
    session_config = (
        active_publication.session_config()
        if active_publication is not None
        else unconfigured_session()
    )
    state_store = SQLiteStateStore(database_path)
    identities = IdentityStore(database_path)
    if not identities.admin_access_summary()["password_configured"]:
        runtime_store.begin_installation()
    identities.reconcile_panels(set(session_config.panels))
    engine = TrafficEngine(
        session_config,
        state_store=state_store,
    )
    pairing = PairingService(
        identities,
        set(engine.config.panels),
    )
    connection_code = args.pairing_code or _load_or_create_connection_code(state_directory)
    identities.revoke_pairing_codes(label="Lokal enkel parkoppling")
    pairing_code = connection_code
    # Only a code that was actually issued may reach the screens; without panels
    # there is nothing to pair with and the code would not work.
    issued_code = ""
    if engine.config.panels:
        # The code is printed on the meeting's screens, so it defaults to never
        # expiring. An administrator can still give it a lifetime.
        validity_hours = runtime_store.connection_code_validity_hours()
        pairing_code = identities.issue_pairing_code(
            list(engine.config.panels),
            allowed_kinds=[
                DeviceKind.SWIFT_PANEL,
                DeviceKind.SWIFT_ADMIN,
                DeviceKind.WEB_ADMIN,
                DeviceKind.TKL_TERMINAL,
                # A physical TMBox keys this same code in directly (Wi-Fi
                # portal or its own keypad), so it needs to be an allowed
                # kind here too - otherwise the code shown on the connection
                # badge would be rejected from the one device it is for.
                DeviceKind.ESP32_PANEL,
            ],
            ttl=timedelta(hours=validity_hours) if validity_hours else None,
            max_uses=50,
            label="Lokal enkel parkoppling",
            code=connection_code,
        )
        issued_code = pairing_code

    gateway = MQTTGatewayAdapter(
        engine,
        host=broker_host,
        port=args.mqtt_port,
        gateway_id=args.gateway_id,
        identities=identities,
    )
    gateway.client.connect(broker_host, args.mqtt_port, keepalive=10, clean_start=True)
    gateway.client.loop_start()

    # Protocol v2 runs beside v1 on its own prefix and its own client. There is
    # no bridge between them; a box speaks one or the other.
    station_service = TMBoxStationService(runtime_store, operations_store, identities)
    v2_gateway = TMBoxV2Gateway(
        station_service,
        identities,
        gateway_id=args.gateway_id,
        publish=lambda topic, payload, retain: None,
    )
    v2_adapter = MQTTV2Adapter(v2_gateway, host=broker_host, port=args.mqtt_port)
    v2_adapter.connect()
    discovery_advertiser = _start_discovery_advertiser(
        args.mqtt_port, server_id=args.gateway_id
    )

    local_ip = args.advertised_host.strip() or _local_ip()
    application = TrainMeetHTTPApplication(
        engine,
        identities,
        pairing,
        HTTPServerConfig(
            gateway_id=args.gateway_id,
            mqtt_port=args.mqtt_port,
            local_development=sys.platform == "darwin",
            central_runtime_url=args.central_url,
            allow_restart=True,
            allow_software_update=supports_updates(),
            state_dir=str(state_directory),
            force_external_auth=args.force_external_auth,
            http_port=args.http_port,
            local_ip=local_ip,
            connection_code=issued_code,
        ),
        runtime_store=runtime_store,
        local_configuration_store=local_configuration_store,
        operations_store=operations_store,
        station_service=station_service,
    )
    server = TrainMeetHTTPServer((args.bind, args.http_port), application)
    cloud_sync_stop = threading.Event()
    cloud_sync_thread = threading.Thread(
        target=_cloud_auto_sync_loop,
        args=(application, server, cloud_sync_stop),
        name="trainmeet-cloud-sync",
        daemon=True,
    )
    cloud_sync_thread.start()
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    _print_ready(local_ip, args.http_port, args.mqtt_port, pairing_code)

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nTMBox-servern stoppas …")
    finally:
        cloud_sync_stop.set()
        cloud_sync_thread.join(timeout=2)
        server.shutdown()
        server.server_close()
        gateway.client.disconnect()
        gateway.client.loop_stop()
        v2_adapter.disconnect()
        _stop_process(discovery_advertiser)
        identities.close()
        state_store.close()
        runtime_store.close()
        local_configuration_store.close()
        operations_store.close()
        if broker is not None:
            broker.terminate()
            try:
                broker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                broker.kill()
                broker.wait(timeout=5)
    if server.operational_reset_requested:
        _reset_operational_state(database_path, state_directory)
    elif server.factory_reset_requested:
        _reset_server_state(database_path, state_directory)
    elif server.restore_requested is not None:
        _restore_from_backup(server.restore_requested, database_path)
    if server.restart_requested:
        print("TrainMeet Server startar om …")
        os.execv(
            sys.executable,
            [sys.executable, "-m", "tmbox_gateway.local_server", *sys.argv[1:]],
        )


def _restore_from_backup(backup_path: Path, database_path: Path) -> None:
    """Lägg tillbaka kopian nu när ingen har databasen öppen.

    Här, och ingen annanstans: alla stores är stängda, WAL-loggen är
    checkpointad och processen är på väg att starta om. Skulle bytet ske medan
    servern kör vore det samma fel som backupen en gång hade - en fil som ser
    hel ut men inte är det.

    Misslyckas det står den gamla databasen kvar orörd. Återställningen skriver
    till ett tillfälligt namn och byter in det först när kopian ligger på plats,
    så en avbruten återställning lämnar ingen halv databas efter sig.
    """

    try:
        backup.restore(backup_path, database_path)
    except Exception as error:  # noqa: BLE001 - se nedan
        # Brett med flit, och bara här. Det här är sista koden som kör innan
        # os.execv startar om tjänsten: ett undantag som slipper förbi lämnar
        # servern nere i stället för att lämna den med sin gamla databas.
        # Återställningen skriver till ett tillfälligt namn och byter in det
        # sist, så ett avbrott kostar återställningen - inte databasen.
        LOGGER.error("Återställningen misslyckades och databasen är orörd: %s", error)
        return
    LOGGER.warning("TrainMeet Server är återställd från %s och startar om", backup_path.name)


def _reset_server_state(database_path: Path, state_directory: Path) -> None:
    """Remove local operational identity and configuration while keeping the installation."""
    for path in (
        database_path,
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
        state_directory / "connection-code.txt",
    ):
        path.unlink(missing_ok=True)
    LOGGER.warning("TrainMeet Server är nollställd och startar första installationen")


def _reset_operational_state(database_path: Path, state_directory: Path) -> None:
    """Clear meet/runtime data while retaining remote administrative access."""
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            for table in (
                "engine_state",
                "runtime_clock",
                "train_positions",
                "tkl_shifts",
                "tkl_movement_states",
                "tkl_events",
                "train_readiness",
                "local_configuration_current",
                "local_configuration_revisions",
                "runtime_publications",
                "pairing_codes",
                "client_panels",
                "discovered_devices",
            ):
                connection.execute(f"DELETE FROM {table}")
            connection.execute(
                "DELETE FROM clients WHERE kind NOT IN ('web_admin', 'swift_admin')"
            )
            connection.execute("DELETE FROM runtime_settings WHERE key <> 'server_name'")
    finally:
        connection.close()
    (state_directory / "connection-code.txt").unlink(missing_ok=True)
    LOGGER.warning(
        "TrainMeet Server träffdata är nollställd; administratör och serveridentitet behålls"
    )


def _cloud_auto_sync_loop(
    application: TrainMeetHTTPApplication,
    server: TrainMeetHTTPServer,
    stop: threading.Event,
) -> None:
    while not stop.is_set():
        try:
            result = application.auto_sync_cloud_runtime()
            if result.get("pending"):
                # Hämtad, inte aktiverad, och framför allt: ingen omstart. Den
                # här loopen körde tidigare request_restart() på egen hand, så
                # en träff kunde starta om under händerna på tågklareraren för
                # att Cloud råkade publicera. Nu väntar den på ett ja.
                LOGGER.info(
                    "Ny Cloud-revision väntar på granskning: %s",
                    result.get("publication_id"),
                )
        except Exception as error:
            LOGGER.warning("Automatisk Cloud-synk misslyckades: %s", error)
        stop.wait(15)


def _database_path(state_directory: Path) -> Path:
    """Return the operational database, carrying a pre-rename one over.

    Installations from before the TMBox rename hold tambox.db. Renaming it
    once, before any connection is opened, keeps their meeting configuration
    and traffic history intact. The write-ahead log and shared-memory files
    have to travel with it or SQLite will refuse the database.
    """
    database_path = state_directory / "trainmeet.db"
    legacy_path = state_directory / "tambox.db"
    if database_path.exists() or not legacy_path.exists():
        return database_path
    for suffix in ("", "-wal", "-shm"):
        source = legacy_path.with_name(f"{legacy_path.name}{suffix}")
        if source.exists():
            source.rename(database_path.with_name(f"{database_path.name}{suffix}"))
    LOGGER.info("Driftdatabasen tambox.db har bytt namn till trainmeet.db")
    return database_path


def _start_discovery_advertiser(
    port: int,
    *,
    server_id: str = "",
    protocol_version: int = 2,
) -> subprocess.Popen[bytes] | None:
    """Announce this server on the meeting network as _tmbox._tcp.

    A box resolves the address from this record, so the service name has to be
    the one the firmware looks for. The TXT record carries the highest protocol
    this server serves and a server id, so a box on a network with several
    servers can tell them apart. Both protocols share the broker this record
    points at; they differ only in topic prefix.
    """
    records = [f"protocol={protocol_version}"]
    if server_id:
        records.append(f"server_id={server_id}")
    if sys.platform == "darwin":
        executable = shutil.which("dns-sd")
        command = (
            [executable, "-R", "TrainMeet TMBox", "_tmbox._tcp", "local.", str(port), *records]
            if executable
            else None
        )
    else:
        executable = shutil.which("avahi-publish-service")
        command = (
            [executable, "TrainMeet TMBox", "_tmbox._tcp", str(port), *records]
            if executable
            else None
        )
    if command is None:
        LOGGER.warning(
            "Lokal TMBox-upptäckt saknas; ange Raspberry Pi-adressen i boxens Wi-Fi-portal"
        )
        return None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        LOGGER.warning("Kunde inte annonsera TMBox-servern på nätverket: %s", error)
        return None
    time.sleep(0.05)
    if process.poll() is not None:
        LOGGER.warning(
            "Lokal TMBox-upptäckt kunde inte starta; serveradressen kan anges manuellt"
        )
        return None
    LOGGER.info("Annonserar _tmbox._tcp på port %s", port)
    return process


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _start_broker(bind: str, port: int, state_directory: Path) -> subprocess.Popen[bytes]:
    executable = shutil.which("mosquitto")
    if executable is None:
        raise SystemExit(
            "Mosquitto saknas. På Mac: kör 'brew install mosquitto' en gång och starta sedan igen."
        )
    if _port_is_open("127.0.0.1", port):
        raise SystemExit(
            f"Port {port} används redan. Stoppa den andra MQTT-tjänsten och försök igen."
        )

    broker_directory = state_directory / "mosquitto"
    broker_directory.mkdir(parents=True, exist_ok=True)
    config_path = broker_directory / "mosquitto.conf"
    config_path.write_text(
        "\n".join(
            [
                f"listener {port} {bind}",
                "allow_anonymous true",
                "persistence true",
                f"persistence_location {broker_directory}/",
                "autosave_interval 10",
                "log_dest stdout",
                "log_type warning",
                "log_type error",
                "",
            ]
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen([executable, "-c", str(config_path)])
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("MQTT-tjänsten kunde inte starta")
        if _port_is_open("127.0.0.1", port):
            return process
        time.sleep(0.05)
    process.terminate()
    raise SystemExit("MQTT-tjänsten hann inte starta")


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(timeout, 0)
    while time.monotonic() < deadline:
        if _port_is_open(host, port):
            return True
        time.sleep(0.1)
    return _port_is_open(host, port)


def _local_ip() -> str:
    # Asking the OS which interface would carry outbound traffic is far more
    # reliable than resolving our own hostname: that resolution depends on
    # DNS, mDNS or /etc/hosts state that is frequently missing or wrong (no
    # mDNS record yet, a Docker container's internal hostname, ...), while the
    # routing table reliably points at whichever interface is actually
    # connected - normally the same Wi-Fi a TMBox is on. No packet is sent;
    # UDP connect() only performs a routing decision, so this works even
    # without internet access as long as a local gateway is configured, which
    # a Wi-Fi router hands out by DHCP whether or not it has a WAN uplink.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
        finally:
            probe.close()
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    candidates: list[str] = []
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if not address.startswith("127.") and address not in candidates:
                candidates.append(address)
    except OSError:
        pass
    return candidates[0] if candidates else "127.0.0.1"


def _load_or_create_connection_code(state_directory: Path) -> str:
    path = state_directory / "connection-code.txt"
    try:
        existing = "".join(character for character in path.read_text(encoding="utf-8") if character.isdigit())
        if len(existing) == 6:
            return existing
    except FileNotFoundError:
        pass
    created = f"{secrets.randbelow(1_000_000):06d}"
    path.write_text(created + "\n", encoding="utf-8")
    path.chmod(0o640)
    return created


def _print_ready(local_ip: str, http_port: int, mqtt_port: int, pairing_code: str) -> None:
    border = "═" * 58
    print(f"\n╔{border}╗")
    print("║  TRAINMEET SERVER ÄR IGÅNG".ljust(59) + "║")
    print(f"╠{border}╣")
    print(f"║  Webb på denna Mac: http://127.0.0.1:{http_port}".ljust(59) + "║")
    print(f"║  Telefon/lokal enhet: http://{local_ip}:{http_port}".ljust(59) + "║")
    print(f"║  MQTT: {local_ip}:{mqtt_port}".ljust(59) + "║")
    print(f"║  Anslutningskod: {pairing_code}".ljust(59) + "║")
    print(f"╚{border}╝\n")
    print("Tryck Ctrl-C för att stoppa. Konfiguration och trafikläge sparas automatiskt.\n")


if __name__ == "__main__":
    main()
