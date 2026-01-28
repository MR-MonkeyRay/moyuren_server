"""Data fetching service module."""

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import FetchEndpointConfig
from app.core.errors import FetchError


class DataFetcher:
    """Asynchronous data fetcher for multiple API endpoints."""

    def __init__(self, endpoints: list[FetchEndpointConfig], logger: logging.Logger) -> None:
        """Initialize the data fetcher.

        Args:
            endpoints: List of endpoint configurations.
            logger: Logger instance for logging request status.
        """
        self.endpoints = endpoints
        self.logger = logger

    async def fetch_endpoint(self, endpoint: FetchEndpointConfig) -> dict[str, Any] | None:
        """Fetch data from a single endpoint.

        Args:
            endpoint: The endpoint configuration to fetch from.

        Returns:
            The response data as dict, or None if request fails.
        """
        try:
            self.logger.debug(f"Fetching from endpoint: {endpoint.name}")

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    endpoint.url,
                    params=endpoint.params,
                    timeout=httpx.Timeout(endpoint.timeout_sec),
                )
                response.raise_for_status()

                data = response.json()
                self.logger.info(f"Successfully fetched from {endpoint.name}")
                return data

        except httpx.TimeoutException:
            self.logger.warning(f"Timeout fetching from {endpoint.name}")
            return None
        except httpx.HTTPStatusError as e:
            self.logger.warning(
                f"HTTP error fetching from {endpoint.name}: {e.response.status_code}"
            )
            return None
        except httpx.RequestError as e:
            self.logger.warning(f"Request error fetching from {endpoint.name}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error fetching from {endpoint.name}: {e}")
            return None

    async def fetch_all(self) -> dict[str, dict[str, Any] | None]:
        """Fetch data from all configured endpoints in parallel.

        Returns:
            A dictionary mapping endpoint names to their fetched data.
            Failed endpoints will have None as value.
        """
        self.logger.info(f"Fetching from {len(self.endpoints)} endpoints")

        tasks = [self.fetch_endpoint(ep) for ep in self.endpoints]
        results = await asyncio.gather(*tasks)

        return {ep.name: result for ep, result in zip(self.endpoints, results)}
