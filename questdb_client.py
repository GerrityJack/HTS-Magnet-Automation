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
    conf = f"http::addr={cfg.QUESTDB_HOST}:{cfg.QUESTDB_PORT};"
    return Sender.from_conf(conf)
