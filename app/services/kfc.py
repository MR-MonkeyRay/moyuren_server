"""KFC Crazy Thursday service module."""

import logging
import httpx
from typing import Any

from app.core.config import CrazyThursdayConfig

logger = logging.getLogger(__name__)


class KfcService:
    """Service for fetching KFC Crazy Thursday content."""

    def __init__(self, config: CrazyThursdayConfig):
        """Initialize the service with configuration.

        Args:
            config: Crazy Thursday configuration.
        """
        self.config = config

    async def fetch_kfc_copy(self) -> str | None:
        """Fetch KFC crazy thursday copy.
        
        Returns:
            The content string if successful, None otherwise.
        """
        if not self.config.enabled:
            return None

        async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
            try:
                resp = await client.get(self.config.url)
                resp.raise_for_status()
                data = resp.json()
                
                # Viki API structure handling
                # Expected format: {"code": 200, "data": {"kfc": "..."}}
                content = None
                if isinstance(data, dict):
                    data_field = data.get("data")
                    if isinstance(data_field, dict):
                        content = data_field.get("kfc")
                    elif isinstance(data_field, str):
                        content = data_field
                    else:
                        content = data.get("text")
                elif isinstance(data, str):
                    content = data

                if content:
                    # Handle escaped newlines in the text
                    content = str(content).strip().replace("\\n", "\n")
                    return content
                
                logger.warning("Empty content received from KFC endpoint")
                return None
                
            except httpx.TimeoutException:
                logger.warning("Timeout fetching KFC content")
            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP {e.response.status_code} from KFC endpoint")
            except Exception as e:
                logger.warning(f"Failed to fetch KFC content: {e}")
            
        return None
