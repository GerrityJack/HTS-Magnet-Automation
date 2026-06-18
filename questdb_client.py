# questdb_client.py
# ─────────────────────────────────────────────────────────────────────────────
# Creates a configured QuestDB Sender. Imported by the logger script.
# ─────────────────────────────────────────────────────────────────────────────

from questdb.ingress import Sender
import mqtt_config as cfg

def get_sender() -> Sender:
    """
    Returns a Sender instance pointed at the QuestDB server in mqtt_config.
    Use as a context manager:
        with get_sender() as sender:
            sender.row(...)
            sender.flush()
    """
    # protocol_version=2 skips the automatic server version detection
    # that can cause "Could not detect server line protocol version" errors
    # when the server is still starting up or when auto-detection tries https.
    conf = (
        f"http::addr={cfg.QUESTDB_HOST}:{cfg.QUESTDB_PORT};"
        "protocol_version=2;"
        "auto_flush=off;"
    )
    return Sender.from_conf(conf)
