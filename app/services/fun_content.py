"""Fun content fetching service module."""

import logging
import random
from datetime import date
from typing import Any

import httpx

from app.core.config import FunContentConfig, FunContentEndpointConfig

logger = logging.getLogger(__name__)


class FunContentService:
    """Service for fetching fun content from various APIs."""

    _DEFAULT_CONTENT = {
        "title": "🐟 摸鱼小贴士",
        "content": "工作再忙，也要记得摸鱼。适当休息，效率更高！"
    }

    def __init__(self, config: FunContentConfig):
        """Initialize the service with configuration.

        Args:
            config: Fun content configuration.
        """
        self.config = config

    async def fetch_content(self, target_date: date) -> dict[str, str]:
        """Fetch fun content with date-based random selection.

        Uses the date as a seed to ensure consistent selection for the same day,
        with fallback to other endpoints if the primary one fails.

        Args:
            target_date: The date to use for random seed.

        Returns:
            Dictionary with 'title' and 'content' keys.
        """
        endpoints = self._shuffle_by_date(target_date)

        async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
            for endpoint in endpoints:
                result = await self._fetch_endpoint(client, endpoint)
                if result:
                    logger.info(f"Successfully fetched content from {endpoint.name}")
                    return result

        logger.warning("All fun content endpoints failed, using default")
        return self._DEFAULT_CONTENT.copy()

    def _shuffle_by_date(self, target_date: date) -> list[FunContentEndpointConfig]:
        """Shuffle endpoints using date as seed for consistent daily selection.

        Args:
            target_date: The date to use for random seed.

        Returns:
            Shuffled list of endpoint configurations.
        """
        seed = int(target_date.strftime("%Y%m%d"))
        rng = random.Random(seed)
        endpoints = list(self.config.endpoints)
        rng.shuffle(endpoints)
        return endpoints

    async def _fetch_endpoint(
        self, client: httpx.AsyncClient, endpoint: FunContentEndpointConfig
    ) -> dict[str, str] | None:
        """Fetch content from a single endpoint.

        Args:
            client: The HTTP client to use for requests.
            endpoint: The endpoint configuration.

        Returns:
            Dictionary with 'title' and 'content' if successful, None otherwise.
        """
        try:
            resp = await client.get(endpoint.url)
            resp.raise_for_status()
            data = resp.json()

            content = self._extract_by_path(data, endpoint.data_path)
            if content and isinstance(content, str) and content.strip():
                return {
                    "title": endpoint.display_title,
                    "content": content.strip()
                }
            logger.debug(f"No valid content from {endpoint.name}")
        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching {endpoint.name}")
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP {e.response.status_code} from {endpoint.name}")
        except httpx.RequestError as e:
            logger.warning(f"Request error fetching {endpoint.name}: {e}")
        except (ValueError, KeyError) as e:
            logger.warning(f"Failed to parse response from {endpoint.name}: {e}")
        return None

    def _extract_by_path(self, data: Any, path: str) -> Any:
        """Extract value from nested dict using dot-separated path.

        Args:
            data: The data to extract from (typically a dict, but may become other types during recursion).
            path: Dot-separated path like 'data.content'.

        Returns:
            The extracted value or None if path is invalid.
        """
        for key in path.split("."):
            if isinstance(data, dict):
                data = data.get(key)
            else:
                return None
        return data
