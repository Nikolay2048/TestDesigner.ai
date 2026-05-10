"""
In-memory state store for the carsharing mock server.
All data lives in `store.db` dict. `store.mock_config` controls mock behaviour.
"""

import copy
from typing import Any


class Store:
    def __init__(self):
        self.db: dict[str, Any] = {
            "users": {},
            "driver_licenses": {},
            "payment_methods": {},
            "vehicles": {},
            "bookings": {},
            "rental_sessions": {},
            "payments": {},
            "fines": {},
            "damage_reports": {},
            "vehicle_telemetry": {},
            "outbox_events": [],
            "audit_logs": [],
        }
        # Controls mock behaviour that the AI agent can toggle via /mock/config
        self.mock_config: dict[str, Any] = {
            "geo_service_available": True,
            "payment_provider_available": True,
            "telemetry_service_available": True,
            "notification_service_available": True,
            # vehicleId values that are "outside allowed zone" for finish
            "vehicles_outside_zone": [],
            # paymentId values for which capture should fail
            "payment_capture_fails": [],
            # vehicleId values with lost telemetry
            "telemetry_lost_vehicles": [],
            # rentalId values that block finish (some critical technical event)
            "rentals_blocked_finish": [],
        }
        self._snapshot: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def save_snapshot(self):
        """Save a deep copy of the current state (for reset)."""
        self._snapshot = copy.deepcopy({"db": self.db, "mock_config": self.mock_config})

    def restore_snapshot(self):
        """Restore to previously saved snapshot."""
        if self._snapshot:
            self.db = copy.deepcopy(self._snapshot["db"])
            self.mock_config = copy.deepcopy(self._snapshot["mock_config"])

    def add_audit(self, entity_type: str, entity_id: str, prev_status: str,
                   new_status: str, reason: str, source: str, correlation_id: str = ""):
        from datetime import datetime, timezone
        self.db["audit_logs"].append({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "prev_status": prev_status,
            "new_status": new_status,
            "reason": reason,
            "source": source,
            "correlation_id": correlation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def add_outbox(self, event_type: str, payload: dict):
        from datetime import datetime, timezone
        import uuid
        self.db["outbox_events"].append({
            "id": str(uuid.uuid4()),
            "event_type": event_type,
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "published": False,
        })


store = Store()
