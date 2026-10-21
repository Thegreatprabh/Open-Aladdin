"""OPEN-ATLAS market-data pipeline (AR-1, phase P1).

Ingestion -> normalizer -> bus (Redis pub/sub + snapshots) -> WebSocket
fan-out. Read-only by construction: this package contains no order
placement code and never will (see docs/LIVE_DATA_PIPELINE.md).
"""

EVENT_VERSION = 1

__all__ = ["EVENT_VERSION"]
