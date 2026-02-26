from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.cache import CacheCleaner
    from app.services.compute import DataComputer
    from app.services.daily_english import CachedDailyEnglishService
    from app.services.fetcher import CachedDataFetcher
    from app.services.fun_content import CachedFunContentService
    from app.services.gold_price import CachedGoldPriceService
    from app.services.holiday import CachedHolidayService
    from app.services.kfc import CachedKfcService
    from app.services.renderer import ImageRenderer
    from app.services.stock_index import StockIndexService


@dataclass
class AppServices:
    """应用服务容器 - 类型安全的服务访问"""

    data_fetcher: "CachedDataFetcher | None"
    holiday_service: "CachedHolidayService"
    fun_content_service: "CachedFunContentService | None"
    kfc_service: "CachedKfcService | None"
    stock_index_service: "StockIndexService | None"
    gold_price_service: "CachedGoldPriceService | None"
    daily_english_service: "CachedDailyEnglishService | None"
    image_renderer: "ImageRenderer"
    data_computer: "DataComputer"
    cache_cleaner: "CacheCleaner"
