"""State file migration utilities."""

from typing import Any


STATE_VERSION = 2


def migrate_state(state_data: dict[str, Any], default_template: str = "moyuren") -> dict[str, Any]:
    """Migrate state data from v1 to v2 format.

    Args:
        state_data: State data dictionary to migrate.
        default_template: Default template name for migration.

    Returns:
        Migrated state data in v2 format.

    Raises:
        ValueError: If state_data is invalid or version is unsupported.
    """
    if not isinstance(state_data, dict):
        raise ValueError("state_data must be a dict")

    version = state_data.get("version")
    if version == STATE_VERSION:
        return state_data
    if version not in (None, 1):
        raise ValueError(f"Unsupported state version: {version}")

    # Extract v1 fields
    filename = state_data.get("filename", "")
    updated = state_data.get("updated") or state_data.get("timestamp") or ""
    updated_at = state_data.get("updated_at") or 0

    public_data = {
        "date": state_data.get("date", ""),
        "timestamp": state_data.get("timestamp", ""),
        "updated": updated,
        "updated_at": updated_at,
        "weekday": state_data.get("weekday", ""),
        "lunar_date": state_data.get("lunar_date", ""),
        "fun_content": state_data.get("fun_content"),
        "countdowns": state_data.get("countdowns", []),
        "is_crazy_thursday": state_data.get("is_crazy_thursday", False),
        "kfc_content": state_data.get("kfc_content"),
    }

    template_specific_data = {
        "date_info": state_data.get("date_info"),
        "weekend": state_data.get("weekend"),
        "solar_term": state_data.get("solar_term"),
        "guide": state_data.get("guide"),
        "news_list": state_data.get("news_list"),
        "news_meta": state_data.get("news_meta"),
        "holidays": state_data.get("holidays"),
        "kfc_content_full": state_data.get("kfc_content_full"),
        "stock_indices": state_data.get("stock_indices"),
    }

    return {
        "version": STATE_VERSION,
        "public": public_data,
        "templates": {
            default_template: {
                "filename": filename,
                "updated": updated,
                "updated_at": updated_at,
            }
        },
        "template_data": {
            default_template: template_specific_data,
        },
        # Backward compatible fields
        **public_data,
        "filename": filename,
        **template_specific_data,
    }
