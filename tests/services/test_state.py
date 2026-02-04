"""Tests for app/services/state.py - state migration utilities."""

from typing import Any

import pytest

from app.services.state import migrate_state, STATE_VERSION


class TestMigrateState:
    """Tests for migrate_state function."""

    def test_v2_state_returns_unchanged(self, sample_v2_state: dict[str, Any]) -> None:
        """Test v2 state is returned unchanged."""
        result = migrate_state(sample_v2_state)
        assert result == sample_v2_state

    def test_v1_state_migrates_to_v2(self, sample_v1_state: dict[str, Any]) -> None:
        """Test v1 state migrates to v2 format."""
        result = migrate_state(sample_v1_state)

        assert result["version"] == STATE_VERSION
        assert "public" in result
        assert "templates" in result
        assert "template_data" in result

    def test_v1_state_preserves_date(self, sample_v1_state: dict[str, Any]) -> None:
        """Test v1 migration preserves date field."""
        result = migrate_state(sample_v1_state)

        assert result["public"]["date"] == sample_v1_state["date"]
        # Backward compatible field
        assert result["date"] == sample_v1_state["date"]

    def test_v1_state_preserves_filename(self, sample_v1_state: dict[str, Any]) -> None:
        """Test v1 migration preserves filename in templates."""
        result = migrate_state(sample_v1_state)

        assert result["templates"]["moyuren"]["filename"] == sample_v1_state["filename"]
        # Backward compatible field
        assert result["filename"] == sample_v1_state["filename"]

    def test_v1_state_preserves_fun_content(self, sample_v1_state: dict[str, Any]) -> None:
        """Test v1 migration preserves fun_content."""
        result = migrate_state(sample_v1_state)

        assert result["public"]["fun_content"] == sample_v1_state["fun_content"]

    def test_v1_state_uses_timestamp_as_updated(self, sample_v1_state: dict[str, Any]) -> None:
        """Test v1 migration uses timestamp as updated."""
        result = migrate_state(sample_v1_state)

        assert result["public"]["updated"] == sample_v1_state["timestamp"]
        assert result["templates"]["moyuren"]["updated"] == sample_v1_state["timestamp"]

    def test_v1_state_with_custom_template_name(self, sample_v1_state: dict[str, Any]) -> None:
        """Test v1 migration with custom template name."""
        result = migrate_state(sample_v1_state, default_template="custom")

        assert "custom" in result["templates"]
        assert "custom" in result["template_data"]

    def test_none_version_treated_as_v1(self) -> None:
        """Test state without version is treated as v1."""
        state = {
            "date": "2026-02-04",
            "timestamp": "2026-02-04T10:00:00+08:00",
            "filename": "test.jpg",
        }
        result = migrate_state(state)

        assert result["version"] == STATE_VERSION

    def test_invalid_state_type_raises_error(self) -> None:
        """Test non-dict state raises ValueError."""
        with pytest.raises(ValueError, match="state_data must be a dict"):
            migrate_state("invalid")  # type: ignore

    def test_unsupported_version_raises_error(self) -> None:
        """Test unsupported version raises ValueError."""
        state = {"version": 99}
        with pytest.raises(ValueError, match="Unsupported state version"):
            migrate_state(state)

    def test_v1_state_with_missing_fields_uses_defaults(self) -> None:
        """Test v1 migration handles missing fields with defaults."""
        minimal_state: dict[str, Any] = {}
        result = migrate_state(minimal_state)

        assert result["version"] == STATE_VERSION
        assert result["public"]["date"] == ""
        assert result["public"]["timestamp"] == ""
        assert result["public"]["countdowns"] == []
        assert result["public"]["is_crazy_thursday"] is False

    def test_v1_state_with_updated_field(self) -> None:
        """Test v1 migration prefers updated over timestamp."""
        state = {
            "date": "2026-02-04",
            "timestamp": "2026-02-04T08:00:00+08:00",
            "updated": "2026-02-04T10:00:00+08:00",
            "filename": "test.jpg",
        }
        result = migrate_state(state)

        assert result["public"]["updated"] == "2026-02-04T10:00:00+08:00"

    def test_v1_state_preserves_template_specific_data(self) -> None:
        """Test v1 migration preserves template-specific data."""
        state = {
            "date": "2026-02-04",
            "timestamp": "2026-02-04T10:00:00+08:00",
            "filename": "test.jpg",
            "date_info": {"year_month": "2026.02"},
            "weekend": {"days_left": 2},
            "solar_term": {"name": "立春"},
            "news_list": [{"num": 1, "text": "News"}],
            "holidays": [{"name": "春节"}],
        }
        result = migrate_state(state)

        assert result["template_data"]["moyuren"]["date_info"] == {"year_month": "2026.02"}
        assert result["template_data"]["moyuren"]["weekend"] == {"days_left": 2}
        assert result["template_data"]["moyuren"]["solar_term"] == {"name": "立春"}
        assert result["template_data"]["moyuren"]["news_list"] == [{"num": 1, "text": "News"}]
        assert result["template_data"]["moyuren"]["holidays"] == [{"name": "春节"}]

    def test_backward_compatible_fields_present(self, sample_v1_state: dict[str, Any]) -> None:
        """Test backward compatible fields are present at root level."""
        result = migrate_state(sample_v1_state)

        # These fields should be at root level for backward compatibility
        assert "date" in result
        assert "timestamp" in result
        assert "weekday" in result
        assert "lunar_date" in result
        assert "filename" in result
