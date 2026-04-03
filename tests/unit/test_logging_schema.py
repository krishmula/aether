"""Tests for extended logging schema (Phase A1)."""

import json
import logging
from io import StringIO

from aether.utils.log import setup_logging, BoundLogger, JSONFormatter


class TestExtendedLoggingSchema:
    """Test the extended observability fields in log output."""

    def setup_method(self):
        """Set up test logger with JSON output."""
        self.log_capture = StringIO()

        # Configure root logger for test
        self.logger = logging.getLogger("aether.test")
        self.logger.handlers = []
        self.logger.setLevel(logging.DEBUG)

        handler = logging.StreamHandler(self.log_capture)
        handler.setFormatter(JSONFormatter())  # Use actual JSONFormatter
        self.logger.addHandler(handler)

    def teardown_method(self):
        """Clean up handlers after each test."""
        self.logger.handlers = []

    def test_event_type_field(self):
        """Test that event_type is included in JSON output."""
        logger = BoundLogger(self.logger, {"event_type": "broker_declared_dead"})
        logger.info("broker failure detected")

        output = self.log_capture.getvalue()
        record = json.loads(output.strip())

        assert record["event_type"] == "broker_declared_dead"
        assert record["message"] == "broker failure detected"

    def test_recovery_correlation_fields(self):
        """Test recovery_id and recovery_path fields."""
        logger = BoundLogger(
            self.logger,
            {
                "recovery_id": "rec-123-abc",
                "recovery_path": "replacement",
                "broker_id": "5",
            },
        )
        logger.info("recovery started")

        record = json.loads(self.log_capture.getvalue().strip())

        assert record["recovery_id"] == "rec-123-abc"
        assert record["recovery_path"] == "replacement"
        assert record["broker_id"] == "5"

    def test_component_identification_fields(self):
        """Test component_type, component_id, service_name fields."""
        logger = BoundLogger(
            self.logger,
            {
                "service_name": "aether-broker",
                "component_type": "broker",
                "component_id": "3",
            },
        )
        logger.info("component started")

        record = json.loads(self.log_capture.getvalue().strip())

        assert record["service_name"] == "aether-broker"
        assert record["component_type"] == "broker"
        assert record["component_id"] == "3"

    def test_snapshot_correlation_field(self):
        """Test snapshot_id field for snapshot operations."""
        logger = BoundLogger(self.logger, {"snapshot_id": "snap-xyz-789"})
        logger.info("snapshot completed")

        record = json.loads(self.log_capture.getvalue().strip())
        assert record["snapshot_id"] == "snap-xyz-789"

    def test_error_kind_field(self):
        """Test error_kind for categorized errors."""
        logger = BoundLogger(
            self.logger,
            {"event_type": "broker_recovery_failed", "error_kind": "timeout"},
        )
        logger.error("recovery failed")

        record = json.loads(self.log_capture.getvalue().strip())
        assert record["error_kind"] == "timeout"

    def test_legacy_fields_still_work(self):
        """Ensure backward compatibility with existing context fields."""
        logger = BoundLogger(
            self.logger,
            {"broker": "127.0.0.1:5001", "msg_id": "msg-abc-123"},
        )
        logger.info("message received")

        record = json.loads(self.log_capture.getvalue().strip())
        assert record["broker"] == "127.0.0.1:5001"
        assert record["msg_id"] == "msg-abc-123"

    def test_fields_not_set_are_excluded(self):
        """Verify unset fields don't appear in output."""
        logger = BoundLogger(self.logger, {"event_type": "test_event"})
        logger.info("test message")

        record = json.loads(self.log_capture.getvalue().strip())

        # event_type should be present
        assert "event_type" in record
        # unset fields should not be present
        assert "recovery_id" not in record
        assert "snapshot_id" not in record
        assert "error_kind" not in record

    def test_subscriber_id_field(self):
        """Test subscriber_id field."""
        logger = BoundLogger(self.logger, {"subscriber_id": "sub-42"})
        logger.info("subscriber connected")

        record = json.loads(self.log_capture.getvalue().strip())
        assert record["subscriber_id"] == "sub-42"

    def test_publisher_id_field(self):
        """Test publisher_id field."""
        logger = BoundLogger(self.logger, {"publisher_id": "pub-7"})
        logger.info("publisher started")

        record = json.loads(self.log_capture.getvalue().strip())
        assert record["publisher_id"] == "pub-7"

    def test_all_new_fields_together(self):
        """Test all Phase A1 fields in a single log record."""
        context = {
            "service_name": "aether-orchestrator",
            "component_type": "orchestrator",
            "component_id": "orch-1",
            "broker_id": "2",
            "subscriber_id": "sub-5",
            "publisher_id": "pub-3",
            "event_type": "broker_recovery_started",
            "recovery_id": "rec-uuid-123",
            "snapshot_id": "snap-uuid-456",
            "recovery_path": "replacement",
            "error_kind": None,  # Explicitly None should not be emitted
        }

        logger = BoundLogger(self.logger, context)
        logger.info("recovery initiated")

        record = json.loads(self.log_capture.getvalue().strip())

        assert record["service_name"] == "aether-orchestrator"
        assert record["component_type"] == "orchestrator"
        assert record["component_id"] == "orch-1"
        assert record["broker_id"] == "2"
        assert record["subscriber_id"] == "sub-5"
        assert record["publisher_id"] == "pub-3"
        assert record["event_type"] == "broker_recovery_started"
        assert record["recovery_id"] == "rec-uuid-123"
        assert record["snapshot_id"] == "snap-uuid-456"
        assert record["recovery_path"] == "replacement"
        # None values should not be emitted
        assert "error_kind" not in record
