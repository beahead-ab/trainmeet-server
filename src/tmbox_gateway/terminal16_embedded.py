"""Reuse the isolated, bounded lab without exposing any live runtime data."""
from types import SimpleNamespace
from .terminal16_public import PublicHandler


class EmbeddedHandler(PublicHandler):
    def _local(self, *, mutation=False):
        return self.parent._public_clients_allowed() and super()._local(mutation=mutation)


def serve(parent, method):
    handler = EmbeddedHandler.__new__(EmbeddedHandler)
    handler.__dict__.update(parent.__dict__)
    handler.parent = parent
    handler.session = handler.session_token = handler.cookie_value = None
    host = parent.headers.get("Host", "")
    origin = parent.server.application.config.public_client_origin or "http://" + host
    handler.server = SimpleNamespace(origin=origin, host=host, prefix="/tmbox-lab/",
                                     sessions=parent.server.application.lab_sessions)
    getattr(handler, "do_" + method)()
