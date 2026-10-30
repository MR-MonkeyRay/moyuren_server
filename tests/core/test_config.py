"""Tests for app/core/config.py - configuration management."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import (
    AppConfig,
    CacheConfig,
    CrazyThursdaySource,
    FunContentEndpoint,
    FunContentSource,
    HolidaySource,
    InstancesConfig,
    LoggingConfig,
    NetworkConfig,
    NewsSource,
    OpsConfig,
    SchedulerConfig,
    ServerConfig,
    SQLiteBackendConfig,
    StockIndexItem,
    StockIndexSource,
    TemplateItemConfig,
    TemplateRenderConfig,
    TemplatesConfig,
    TextLimitsConfig,
    TimezoneConfig,
    ViewportConfig,
    load_config,
)


class TestSQLiteBackendConfig:
    """Tests for SQLite dictionary path configuration."""

    def test_default_db_path(self) -> None:
        """Test the default path is relative to the cache directory."""
        config = SQLiteBackendConfig()

        assert config.db_path == "ecdict/stardict.db"
        assert config.resolve_db_path("/tmp/c") == Path("/tmp/c/ecdict/stardict.db")

    @pytest.mark.parametrize(
        ("db_path", "expected"),
        [
            ("ecdict/stardict.db", "/tmp/c/ecdict/stardict.db"),
            ("cache/ecdict/stardict.db", "/tmp/c/cache/ecdict/stardict.db"),
            ("/opt/ecdict/stardict.db", "/opt/ecdict/stardict.db"),
            ("/cache/ecdict/stardict.db", "/cache/ecdict/stardict.db"),
            ("cache-other/stardict.db", "/tmp/c/cache-other/stardict.db"),
        ],
    )
    def test_resolve_db_path(self, db_path: str, expected: str) -> None:
        """Test relative paths retain every component and absolute paths stay absolute."""
        config = SQLiteBackendConfig(db_path=db_path)

        assert config.resolve_db_path(Path("/tmp/c")) == Path(expected)


class TestServerConfig:
    """Tests for ServerConfig model."""

    def test_valid_server_config(self) -> None:
        """Test valid server configuration."""
        config = ServerConfig(
            host="0.0.0.0", port=8000, base_domain="http://localhost:8000"
        )
        assert config.host == "0.0.0.0"
        assert config.port == 8000


class TestSchedulerConfig:
    """Tests for SchedulerConfig model."""

    def test_valid_daily_times(self) -> None:
        """Test valid daily times."""
        config = SchedulerConfig(daily_times=["06:00", "18:00"])
        assert len(config.daily_times) == 2

    def test_valid_hourly_mode_config(self) -> None:
        """Test valid hourly mode config."""
        config = SchedulerConfig(mode="hourly", minute_of_hour=30)
        assert config.mode == "hourly"
        assert config.minute_of_hour == 30

    def test_invalid_time_format_raises_error(self) -> None:
        """Test invalid time format raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(daily_times=["25:00"])
        assert "Invalid time format" in str(exc_info.value)

    def test_empty_daily_times_raises_error(self) -> None:
        """Test empty daily times raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(daily_times=[])
        assert "cannot be empty" in str(exc_info.value)

    def test_empty_daily_times_allowed_in_hourly_mode(self) -> None:
        """Test empty daily times is allowed in hourly mode."""
        config = SchedulerConfig(mode="hourly", daily_times=[], minute_of_hour=0)
        assert config.mode == "hourly"
        assert config.daily_times == []

    @pytest.mark.parametrize("minute", [-1, 60])
    def test_invalid_minute_of_hour_raises_error(self, minute: int) -> None:
        """Test invalid minute_of_hour raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(mode="hourly", minute_of_hour=minute)
        assert "minute_of_hour must be between 0 and 59" in str(exc_info.value)

    def test_strips_whitespace(self) -> None:
        """Test strips whitespace from time strings."""
        config = SchedulerConfig(daily_times=["  06:00  "])
        assert config.daily_times == ["06:00"]


class TestCacheConfig:
    """Tests for CacheConfig model."""

    def test_valid_retain_days(self) -> None:
        """Test valid retain_days."""
        config = CacheConfig(retain_days=30)
        assert config.retain_days == 30

    def test_default_max_images_per_template_per_day(self) -> None:
        """Test the default daily image limit."""
        config = CacheConfig()
        assert config.max_images_per_template_per_day == 3

    def test_valid_max_images_per_template_per_day(self) -> None:
        """Test a positive daily image limit."""
        config = CacheConfig(max_images_per_template_per_day=5)
        assert config.max_images_per_template_per_day == 5

    @pytest.mark.parametrize("limit", [0, -1])
    def test_invalid_max_images_per_template_per_day_raises_error(
        self, limit: int
    ) -> None:
        """Test non-positive daily image limits are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CacheConfig(max_images_per_template_per_day=limit)
        assert "must be at least 1" in str(exc_info.value)

    def test_max_images_per_template_per_day_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test the daily image limit can be overridden by environment."""
        monkeypatch.setenv("MOYUREN_CACHE_MAX_IMAGES_PER_TEMPLATE_PER_DAY", "7")
        config = AppConfig.from_yaml("config.yaml")
        assert config.cache.max_images_per_template_per_day == 7

    def test_zero_retain_days_raises_error(self) -> None:
        """Test zero retain_days raises error."""
        with pytest.raises(ValidationError) as exc_info:
            CacheConfig(retain_days=0)
        assert "must be positive" in str(exc_info.value)

    def test_negative_retain_days_raises_error(self) -> None:
        """Test negative retain_days raises error."""
        with pytest.raises(ValidationError):
            CacheConfig(retain_days=-1)


class TestOpsConfig:
    """Tests for OpsConfig model."""

    def test_default_api_key(self) -> None:
        """Test default api_key is empty string."""
        config = OpsConfig()
        assert config.api_key == ""

    def test_custom_api_key(self) -> None:
        """Test custom api_key."""
        config = OpsConfig(api_key="test-key-123")
        assert config.api_key == "test-key-123"


class TestNetworkConfig:
    """Tests for global network configuration."""

    def test_default_proxy_is_none(self) -> None:
        """Test default proxy is disabled."""
        config = NetworkConfig()
        assert config.proxy_url is None

    @pytest.mark.parametrize(
        "proxy_url",
        [
            "http://proxy.example:8080",
            "http://user:password@proxy.example:8080",
            "socks5://proxy.example:1080",
        ],
    )
    def test_valid_proxy_url(self, proxy_url: str) -> None:
        """Test supported proxy URL formats."""
        config = NetworkConfig(proxy_url=proxy_url)
        assert config.proxy_url == proxy_url

    @pytest.mark.parametrize(
        "proxy_url",
        [
            "ftp://proxy.example:21",
            "https://proxy.example:8443",
            "http://",
            "http://proxy.example/path",
            "http://proxy.example:8080?token=value",
            "http://proxy.example:8080#frag",
        ],
    )
    def test_invalid_proxy_url_raises_error(self, proxy_url: str) -> None:
        """Test invalid proxy URLs are rejected."""
        with pytest.raises(ValidationError):
            NetworkConfig(proxy_url=proxy_url)

    @pytest.mark.parametrize(
        "userinfo",
        ["user:proxy-secret", "user", ":proxy-secret", "user:", "", ":"],
    )
    def test_authenticated_socks_proxy_raises_safe_error(self, userinfo: str) -> None:
        """Test SOCKS userinfo is rejected without exposing credentials."""
        proxy_url = f"socks5://{userinfo}@proxy.example:1080"
        with pytest.raises(ValidationError) as exc_info:
            NetworkConfig(proxy_url=proxy_url)

        message = str(exc_info.value)
        assert "Chromium cannot authenticate to SOCKS" in message
        assert "unauthenticated SOCKS" in message
        assert "HTTP proxy with credentials" in message
        assert "gost/privoxy" in message
        assert "proxy-secret" not in message
        assert proxy_url not in message

    def test_load_config_rejects_authenticated_socks_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test application and script config loading hides rejected proxy secrets."""
        monkeypatch.setenv(
            "MOYUREN_NETWORK_PROXY_URL",
            "socks5://user:proxy-secret@proxy.example:1080",
        )
        with pytest.raises(ValueError) as exc_info:
            load_config("config.yaml")

        message = str(exc_info.value)
        assert "Configuration validation failed" in message
        assert "Chromium" in message
        assert "proxy-secret" not in message


class TestInstancesConfig:
    """Tests for global 60s public instance configuration."""

    def test_defaults_disable_instance_switching(self) -> None:
        """Test an empty configuration performs no instance switching."""
        config = InstancesConfig()
        assert config.list_url is None
        assert config.urls is None
        assert config.allowlist is None

    def test_normalizes_allowlist(self) -> None:
        """Bare hostnames and subdomain wildcards are normalized to lowercase."""
        config = InstancesConfig(allowlist=[" API.Example.COM ", "*.Example.COM"])
        assert config.allowlist == ["api.example.com", "*.example.com"]
        assert InstancesConfig(allowlist=[]).allowlist == []

    def test_allowlist_environment_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The discovered-host allowlist can be supplied as a JSON environment list."""
        monkeypatch.setenv("MOYUREN_INSTANCES_ALLOWLIST", '["API.Example.COM", "*.example.org"]')
        config = AppConfig.from_yaml("config.yaml")
        assert config.instances.allowlist == ["api.example.com", "*.example.org"]

    @pytest.mark.parametrize("pattern", [
        "", "https://example.com", "example.com/path", "example.com:443",
        "user@example.com", "*example.com", "*.*.example.com", ".example.com",
        "example..com", "-example.com", "example.com?x=1", "example.com#fragment",
        "example.com.", "a" * 64 + ".com",
    ])
    def test_rejects_invalid_allowlist_pattern(self, pattern: str) -> None:
        """An instance pattern cannot contain URL syntax or invalid labels."""
        with pytest.raises(ValidationError):
            InstancesConfig(allowlist=[pattern])

    def test_normalizes_urls(self) -> None:
        """Test trailing slashes and blank entries are removed."""
        config = InstancesConfig(urls=["https://60s.crystelf.top/", "  ", ""])
        assert config.urls == ["https://60s.crystelf.top"]

    def test_empty_urls_become_none(self) -> None:
        """Test an all-blank URL list is treated as unset."""
        assert InstancesConfig(urls=["", "  "]).urls is None

    def test_explicit_null_urls_allowed(self) -> None:
        """Test an explicit null URL list is allowed."""
        assert InstancesConfig(urls=None).urls is None
        assert InstancesConfig(list_url=None).list_url is None

    def test_rejects_unknown_keys(self) -> None:
        """Test unknown keys are rejected."""
        with pytest.raises(ValidationError):
            InstancesConfig(instances=["https://60s.crystelf.top"])


class TestTemplatesConfig:
    """Tests for TemplatesConfig model."""

    def test_get_template_by_name(self) -> None:
        """Test get template by name."""
        config = TemplatesConfig(
            default="main",
            items=[
                TemplateItemConfig(
                    name="main",
                    path="templates/main.html",
                    viewport=ViewportConfig(width=800, height=600),
                ),
                TemplateItemConfig(
                    name="alt",
                    path="templates/alt.html",
                    viewport=ViewportConfig(width=800, height=600),
                ),
            ],
        )
        template = config.get_template("alt")
        assert template.name == "alt"

    def test_get_template_default(self) -> None:
        """Test get template uses default."""
        config = TemplatesConfig(
            default="main",
            items=[
                TemplateItemConfig(
                    name="main",
                    path="templates/main.html",
                    viewport=ViewportConfig(width=800, height=600),
                ),
            ],
        )
        template = config.get_template()
        assert template.name == "main"

    def test_get_template_not_found_raises_error(self) -> None:
        """Test get template not found raises error."""
        config = TemplatesConfig(
            items=[
                TemplateItemConfig(
                    name="main",
                    path="templates/main.html",
                    viewport=ViewportConfig(width=800, height=600),
                )
            ]
        )
        with pytest.raises(ValueError, match="Template not found"):
            config.get_template("nonexistent")

    def test_duplicate_names_raises_error(self) -> None:
        """Test duplicate template names raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplatesConfig(
                items=[
                    TemplateItemConfig(
                        name="main",
                        path="templates/main.html",
                        viewport=ViewportConfig(width=800, height=600),
                    ),
                    TemplateItemConfig(
                        name="main",
                        path="templates/other.html",
                        viewport=ViewportConfig(width=800, height=600),
                    ),
                ]
            )
        assert "unique" in str(exc_info.value)


class TestLoggingConfig:
    """Tests for LoggingConfig model."""

    def test_valid_log_level(self) -> None:
        """Test valid log level."""
        config = LoggingConfig(level="DEBUG")
        assert config.level == "DEBUG"

    def test_case_insensitive_level(self) -> None:
        """Test log level is case insensitive."""
        config = LoggingConfig(level="debug")
        assert config.level == "DEBUG"

    def test_invalid_level_raises_error(self) -> None:
        """Test invalid log level raises error."""
        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(level="INVALID")
        assert "must be one of" in str(exc_info.value)


class TestTimezoneConfig:
    """Tests for TimezoneConfig model."""

    def test_valid_iana_timezone(self) -> None:
        """Test valid IANA timezone."""
        config = TimezoneConfig(business="Asia/Shanghai", display="America/New_York")
        assert config.business == "Asia/Shanghai"

    def test_display_accepts_local(self) -> None:
        """Test display field accepts 'local'."""
        config = TimezoneConfig(business="Asia/Shanghai", display="local")
        assert config.display == "local"

    def test_business_rejects_local(self) -> None:
        """Test business field rejects 'local'."""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="local", display="local")
        assert "does not accept 'local'" in str(exc_info.value)

    def test_valid_utc_offset(self) -> None:
        """Test valid UTC offset format."""
        config = TimezoneConfig(business="UTC+8", display="UTC-5")
        assert config.business == "UTC+8"

    def test_invalid_timezone_raises_error(self) -> None:
        """Test invalid timezone raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="Invalid/Zone")
        assert "Invalid timezone" in str(exc_info.value)

    def test_utc_offset_out_of_range_raises_error(self) -> None:
        """Test UTC offset out of range raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="UTC+15")
        assert "Invalid timezone offset" in str(exc_info.value)


class TestHolidaySource:
    """Tests for HolidaySource model."""

    def test_valid_holiday_config(self) -> None:
        """Test valid holiday configuration."""
        config = HolidaySource(type="holiday", timeout_sec=10)
        assert config.timeout_sec == 10

    def test_obsolete_mirror_urls_are_rejected(self) -> None:
        """Reject removed fields rather than silently discarding configuration."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            HolidaySource.model_validate({"mirror_urls": ["https://mirror.example"]})

    def test_zero_timeout_raises_error(self) -> None:
        """Test zero timeout raises error."""
        with pytest.raises(ValidationError):
            HolidaySource(type="holiday", timeout_sec=0)

    @pytest.mark.parametrize(
        ("holiday_entry", "expected_enabled"),
        [(None, True), ({"type": "holiday", "enabled": False}, False)],
    )
    def test_holiday_defaults_on_unless_explicitly_disabled(
        self,
        tmp_path: Path,
        holiday_entry: dict[str, object] | None,
        expected_enabled: bool,
    ) -> None:
        """Test a config without a holiday entry keeps holidays enabled."""
        import yaml

        raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        raw["data_sources"] = [
            source for source in raw["data_sources"] if source["type"] != "holiday"
        ]
        if holiday_entry is not None:
            raw["data_sources"].append(holiday_entry)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

        config = load_config(str(config_path))

        assert (config.get_source(HolidaySource) is not None) is expected_enabled


class TestCrazyThursdaySource:
    """Tests for CrazyThursdaySource model."""

    def test_valid_config(self) -> None:
        """Test valid crazy thursday configuration."""
        config = CrazyThursdaySource(
            type="crazy_thursday",
            enabled=True,
            url="https://api.example.com/kfc",
            timeout_sec=5,
        )
        assert config.enabled is True

    def test_disabled_config(self) -> None:
        """Test disabled configuration."""
        config = CrazyThursdaySource(
            type="crazy_thursday",
            enabled=False,
            url="https://api.example.com/kfc",
            timeout_sec=5,
        )
        assert config.enabled is False


class TestStockIndexSource:
    """Tests for StockIndexSource model."""

    def test_valid_config(self) -> None:
        """Test valid stock index configuration."""
        config = StockIndexSource(
            type="stock_index",
            quote_url="https://api.example.com/quote",
            secids=[
                {"secid": "1.000001", "name": "上证指数", "market": "A"},
                {"secid": "0.399001", "name": "深证成指", "market": "A"},
            ],
            timeout_sec=5,
            market_timezones={"A": "Asia/Shanghai"},
            cache_ttl_sec=60,
        )
        assert len(config.secids) == 2
        assert config.secids == [
            StockIndexItem(
                secid="1.000001", code="000001", name="上证指数", market="A"
            ),
            StockIndexItem(
                secid="0.399001", code="399001", name="深证成指", market="A"
            ),
        ]
        assert config.cache_ttl_sec == 60

    def test_object_items_preserve_configured_order_and_names(self) -> None:
        """Test object items normalize codes while preserving order and names."""
        config = StockIndexSource(
            type="stock_index",
            quote_url="https://api.example.com/quote",
            secids=[
                {"secid": "100.DJIA", "name": "道指", "market": "US"},
                {"secid": "1.000001", "name": "沪指", "market": "A"},
            ],
            market_timezones={"A": "Asia/Shanghai", "US": "America/New_York"},
        )

        assert [item.code for item in config.secids] == ["DJIA", "000001"]
        assert [item.name for item in config.secids] == ["道指", "沪指"]

    @pytest.mark.parametrize("secid", ["1.000001", "116.UNKNOWN", "999.UNKNOWN"])
    def test_string_items_are_rejected(self, secid: str) -> None:
        """Require an explicit name and market instead of inferring legacy entries."""
        with pytest.raises(ValidationError):
            StockIndexSource.model_validate(
                {
                    "quote_url": "https://api.example.com/quote",
                    "secids": [secid],
                    "market_timezones": {"A": "Asia/Shanghai"},
                }
            )

    @pytest.mark.parametrize("secid", ["000001", "x.000001", "1.", "1.A-B"])
    def test_invalid_secid_format_raises_error(self, secid: str) -> None:
        """Test malformed secids are rejected in the supported object form."""
        with pytest.raises(ValidationError, match="secid must match"):
            StockIndexSource(
                type="stock_index",
                quote_url="https://api.example.com/quote",
                secids=[{"secid": secid, "name": "指数", "market": "A"}],
                market_timezones={"A": "Asia/Shanghai"},
            )

    def test_empty_secids_are_rejected(self) -> None:
        """A stock source must configure at least one index."""
        with pytest.raises(ValidationError):
            StockIndexSource(
                quote_url="https://api.example.com/quote",
                secids=[],
                market_timezones={"A": "Asia/Shanghai"},
            )

    @pytest.mark.parametrize("missing_field", ["name", "market"])
    def test_object_items_require_name_and_market(self, missing_field: str) -> None:
        """Reject incomplete objects without restoring inferred defaults."""
        item = {"secid": "1.000001", "name": "上证指数", "market": "A"}
        del item[missing_field]
        with pytest.raises(ValidationError, match="Field required"):
            StockIndexSource.model_validate(
                {
                    "quote_url": "https://api.example.com/quote",
                    "secids": [item],
                    "market_timezones": {"A": "Asia/Shanghai"},
                }
            )

    def test_duplicate_codes_raise_error(self) -> None:
        """Test duplicate codes are rejected even across different prefixes."""
        with pytest.raises(ValidationError, match="duplicate stock index code"):
            StockIndexSource(
                type="stock_index",
                quote_url="https://api.example.com/quote",
                secids=[
                    {"secid": "1.000001", "name": "沪指", "market": "A"},
                    {"secid": "0.000001", "name": "重复", "market": "A"},
                ],
                market_timezones={"A": "Asia/Shanghai"},
            )

    def test_item_market_must_have_timezone(self) -> None:
        """Test every configured item market has a timezone mapping."""
        with pytest.raises(ValidationError, match="must be a key of market_timezones"):
            StockIndexSource(
                type="stock_index",
                quote_url="https://api.example.com/quote",
                secids=[{"secid": "100.HSI", "name": "恒生指数", "market": "HK"}],
                market_timezones={"A": "Asia/Shanghai"},
            )


class TestTextLimitsConfig:
    """Tests for caps on remote text and news count."""

    def test_defaults(self) -> None:
        """Every text limit has a bounded positive default."""
        expected = {
            "news_item": 120,
            "news_items": 15,
            "fun_content": 300,
            "kfc_text": 300,
            "daily_english_definition": 200,
            "daily_english_translation": 200,
            "holiday_name": 20,
        }
        assert TextLimitsConfig().model_dump() == expected
        assert TemplateRenderConfig().text_limits.model_dump() == expected

    @pytest.mark.parametrize("field", list(TextLimitsConfig.model_fields))
    @pytest.mark.parametrize("value", [0, -1, 1.5, True, "12"])
    def test_requires_positive_integers(self, field: str, value: object) -> None:
        """Reject non-positive and non-integer caps for every field."""
        with pytest.raises(ValidationError):
            TextLimitsConfig.model_validate({field: value})

    def test_nested_overrides_keep_other_defaults(self) -> None:
        """A partial render configuration only overrides the requested cap."""
        config = TemplateRenderConfig.model_validate({"text_limits": {"news_item": 10}})
        assert config.text_limits.news_item == 10
        assert config.text_limits.news_items == 15


class TestTemplateRenderConfig:
    """Tests for TemplateRenderConfig model."""

    def test_default_redirect_limit(self) -> None:
        """Resource fetches follow at most five validated redirects by default."""
        assert TemplateRenderConfig().remote_resource_max_redirects == 5
        assert TemplateRenderConfig(remote_resource_max_redirects=0).remote_resource_max_redirects == 0

    @pytest.mark.parametrize("limit", [-1, 21, 1.5, True, "5"])
    def test_rejects_invalid_redirect_limit(self, limit: object) -> None:
        """Redirect bounds require an integer from zero to twenty."""
        with pytest.raises(ValidationError):
            TemplateRenderConfig.model_validate({"remote_resource_max_redirects": limit})

    def test_default_resource_hosts(self) -> None:
        """Only the existing font CDN hosts are allowed by default."""
        assert TemplateRenderConfig().allowed_resource_hosts == [
            "fonts.googleapis.com", "fonts.googleapis.cn",
            "fonts.gstatic.com", "fonts.gstatic.cn",
        ]

    def test_normalizes_resource_hosts(self) -> None:
        """Resource hosts are bare lowercase hostnames; an empty list denies all."""
        assert TemplateRenderConfig(allowed_resource_hosts=[" CDN.Example.COM "]).allowed_resource_hosts == ["cdn.example.com"]
        assert TemplateRenderConfig(allowed_resource_hosts=[]).allowed_resource_hosts == []

    @pytest.mark.parametrize("hostname", [
        "", "https://example.com", "example.com/path", "example.com:443",
        "*.example.com", "user@example.com", "example.com?x=1", "example.com#x",
        "example..com", "-example.com", "example.com.", "a" * 64 + ".com",
    ])
    def test_rejects_invalid_resource_hosts(self, hostname: str) -> None:
        """Resource allowlist entries cannot carry schemes, ports or paths."""
        with pytest.raises(ValidationError):
            TemplateRenderConfig(allowed_resource_hosts=[hostname])

    def test_valid_config(self) -> None:
        """Test valid template render configuration."""
        config = TemplateRenderConfig(
            device_scale_factor=2,
            jpeg_quality=90,
            use_china_cdn=True,
            remote_resource_cache_ttl_sec=3600,
            remote_resource_timeout_sec=2.5,
            page_load_timeout_sec=8.0,
            font_ready_timeout_sec=1.5,
        )
        assert config.device_scale_factor == 2
        assert config.jpeg_quality == 90
        assert config.remote_resource_cache_ttl_sec == 3600
        assert config.remote_resource_timeout_sec == 2.5
        assert config.page_load_timeout_sec == 8.0
        assert config.font_ready_timeout_sec == 1.5

    def test_zero_scale_raises_error(self) -> None:
        """Test zero device scale factor raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(device_scale_factor=0)
        assert "must be positive" in str(exc_info.value)

    def test_invalid_jpeg_quality_raises_error(self) -> None:
        """Test invalid JPEG quality raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(jpeg_quality=101)
        assert "must be between 1 and 100" in str(exc_info.value)

    def test_invalid_remote_resource_cache_ttl_raises_error(self) -> None:
        """Test invalid remote resource cache TTL raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_cache_ttl_sec=0)
        assert "remote_resource_cache_ttl_sec must be positive" in str(exc_info.value)

    def test_invalid_remote_resource_timeout_raises_error(self) -> None:
        """Test invalid remote resource timeout raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_timeout_sec=0)
        assert "remote_resource_timeout_sec must be positive" in str(exc_info.value)

    def test_invalid_page_load_timeout_raises_error(self) -> None:
        """Test invalid page load timeout raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(page_load_timeout_sec=0)
        assert "page_load_timeout_sec must be positive" in str(exc_info.value)

    def test_font_ready_timeout_exceeds_60_raises_error(self) -> None:
        """Test font readiness timeout exceeding 60 seconds raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(font_ready_timeout_sec=61.0)
        assert "font_ready_timeout_sec must not exceed 60.0" in str(exc_info.value)

    def test_max_size_kb_valid(self) -> None:
        """Test valid remote resource max size."""
        config = TemplateRenderConfig(remote_resource_max_size_kb=5120)
        assert config.remote_resource_max_size_kb == 5120

    def test_max_size_kb_zero_raises_error(self) -> None:
        """Test zero max size raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_max_size_kb=0)
        assert "must be between 1 and 51200" in str(exc_info.value)

    def test_max_size_kb_too_large_raises_error(self) -> None:
        """Test max size exceeding 51200 raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_max_size_kb=51201)
        assert "must be between 1 and 51200" in str(exc_info.value)

    def test_ttl_exceeds_one_year_raises_error(self) -> None:
        """Test TTL exceeding one year raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_cache_ttl_sec=365 * 24 * 60 * 60 + 1)
        assert "must not exceed 1 year" in str(exc_info.value)

    def test_timeout_exceeds_60_raises_error(self) -> None:
        """Test timeout exceeding 60 seconds raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_timeout_sec=61.0)
        assert "must not exceed 60.0" in str(exc_info.value)


class TestNewsSource:
    """Tests for NewsSource model."""

    def test_valid_config(self) -> None:
        """Test valid news source configuration."""
        config = NewsSource(
            type="news", url="https://api.example.com/news", params={"key": "value"}
        )
        assert config.type == "news"
        assert config.url == "https://api.example.com/news"

    def test_empty_url_raises_error(self) -> None:
        """Test empty URL raises error."""
        with pytest.raises(ValidationError) as exc_info:
            NewsSource(type="news", url="")
        assert "cannot be empty" in str(exc_info.value)


class TestFunContentSource:
    """Tests for FunContentSource model."""

    def test_valid_config(self) -> None:
        """Test valid fun content source configuration."""
        config = FunContentSource(
            type="fun_content",
            endpoints=[
                FunContentEndpoint(
                    name="joke",
                    url="https://api.example.com/joke",
                    data_path="data",
                    display_title="笑话",
                )
            ],
        )
        assert config.type == "fun_content"
        assert len(config.endpoints) == 1

    def test_empty_endpoints_raises_error(self) -> None:
        """Test empty endpoints raises error."""
        with pytest.raises(ValidationError) as exc_info:
            FunContentSource(type="fun_content", endpoints=[])
        assert "cannot be empty" in str(exc_info.value)
