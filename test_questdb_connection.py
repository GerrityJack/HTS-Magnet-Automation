"""
test_questdb_connection.py
─────────────────────────────────────────────────────────────────────────────
Writes a handful of dummy rows directly to QuestDB to confirm the connection
and table creation work before any real hardware is connected.

Run this once:
    python test_questdb_connection.py

If it succeeds, go to http://localhost:9000 and run:
    SELECT * FROM labjack_metrics LIMIT 10;

You should see 5 rows of test data. Once confirmed, delete this script —
it is only needed for pipeline verification.
─────────────────────────────────────────────────────────────────────────────
"""

import sys
import time

try:
    from questdb.ingress import Sender, TimestampNanos
    import mqtt_config as cfg
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

print(f"Connecting to QuestDB at {cfg.QUESTDB_HOST}:{cfg.QUESTDB_PORT}...")

conf = f"http::addr={cfg.QUESTDB_HOST}:{cfg.QUESTDB_PORT};"

try:
    with Sender.from_conf(conf) as sender:
        print("Connected. Writing 5 test rows to 'labjack_metrics'...")

        for i in range(5):
            sender.row(
                cfg.QUESTDB_TABLE_LABJACK,
                columns={
                    "vacuum_pressure_torr": float(i + 1) * 1e-3,   # fake pressures
                    "vacuum_gauge_volts":   float(i + 1) * 0.5,    # fake voltages
                    "test_row":             True,                    # flag so you can delete these
                },
                at=TimestampNanos.now(),
            )
            print(f"  Row {i+1}: pressure={float(i+1)*1e-3:.3e} Torr")
            time.sleep(0.1)

        sender.flush()
        print("\nFlushed successfully.")
        print("Now run this in the QuestDB console (http://localhost:9000):")
        print("  SELECT * FROM labjack_metrics LIMIT 10;")
        print("\nTo delete the test rows once confirmed:")
        print("  DELETE FROM labjack_metrics WHERE test_row = true;")

except Exception as e:
    print(f"\nFAILED: {e}")
    print("\nThings to check:")
    print(f"  1. Is QuestDB running?  →  docker ps")
    print(f"  2. Is port 9000 reachable?  →  http://localhost:9000")
    print(f"  3. Is QUESTDB_HOST correct in mqtt_config.py?  →  currently '{cfg.QUESTDB_HOST}'")
