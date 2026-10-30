"""app/core/config.py 配置管理测试。"""

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
    """SQLite 词典路径配置测试。"""

    def test_default_db_path(self) -> None:
        """验证默认路径相对于缓存目录。"""
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
        """验证相对路径保留全部组成部分，绝对路径保持不变。"""
        config = SQLiteBackendConfig(db_path=db_path)

        assert config.resolve_db_path(Path("/tmp/c")) == Path(expected)


class TestServerConfig:
    """ServerConfig 模型测试。"""

    def test_valid_server_config(self) -> None:
        """验证有效的服务器配置。"""
        config = ServerConfig(
            host="0.0.0.0", port=8000, base_domain="http://localhost:8000"
        )
        assert config.host == "0.0.0.0"
        assert config.port == 8000


class TestSchedulerConfig:
    """SchedulerConfig 模型测试。"""

    def test_valid_daily_times(self) -> None:
        """验证有效的每日执行时间。"""
        config = SchedulerConfig(daily_times=["06:00", "18:00"])
        assert len(config.daily_times) == 2

    def test_valid_hourly_mode_config(self) -> None:
        """验证有效的每小时调度配置。"""
        config = SchedulerConfig(mode="hourly", minute_of_hour=30)
        assert config.mode == "hourly"
        assert config.minute_of_hour == 30

    def test_invalid_time_format_raises_error(self) -> None:
        """验证无效的时间格式会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(daily_times=["25:00"])
        assert "Invalid time format" in str(exc_info.value)

    def test_empty_daily_times_raises_error(self) -> None:
        """验证每日执行时间列表为空时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(daily_times=[])
        assert "cannot be empty" in str(exc_info.value)

    def test_empty_daily_times_allowed_in_hourly_mode(self) -> None:
        """验证每小时模式允许每日执行时间列表为空。"""
        config = SchedulerConfig(mode="hourly", daily_times=[], minute_of_hour=0)
        assert config.mode == "hourly"
        assert config.daily_times == []

    @pytest.mark.parametrize("minute", [-1, 60])
    def test_invalid_minute_of_hour_raises_error(self, minute: int) -> None:
        """验证无效的 minute_of_hour 会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(mode="hourly", minute_of_hour=minute)
        assert "minute_of_hour must be between 0 and 59" in str(exc_info.value)

    def test_strips_whitespace(self) -> None:
        """验证时间字符串两端的空白会被去除。"""
        config = SchedulerConfig(daily_times=["  06:00  "])
        assert config.daily_times == ["06:00"]


class TestCacheConfig:
    """CacheConfig 模型测试。"""

    def test_valid_retain_days(self) -> None:
        """验证有效的 retain_days。"""
        config = CacheConfig(retain_days=30)
        assert config.retain_days == 30

    def test_default_max_images_per_template_per_day(self) -> None:
        """验证每日图片数量上限的默认值。"""
        config = CacheConfig()
        assert config.max_images_per_template_per_day == 3

    def test_valid_max_images_per_template_per_day(self) -> None:
        """验证每日图片数量上限接受正数。"""
        config = CacheConfig(max_images_per_template_per_day=5)
        assert config.max_images_per_template_per_day == 5

    @pytest.mark.parametrize("limit", [0, -1])
    def test_invalid_max_images_per_template_per_day_raises_error(
        self, limit: int
    ) -> None:
        """验证每日图片数量上限不接受非正数。"""
        with pytest.raises(ValidationError) as exc_info:
            CacheConfig(max_images_per_template_per_day=limit)
        assert "must be at least 1" in str(exc_info.value)

    def test_max_images_per_template_per_day_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证环境变量可以覆盖每日图片数量上限。"""
        monkeypatch.setenv("MOYUREN_CACHE_MAX_IMAGES_PER_TEMPLATE_PER_DAY", "7")
        config = AppConfig.from_yaml("config.yaml")
        assert config.cache.max_images_per_template_per_day == 7

    def test_zero_retain_days_raises_error(self) -> None:
        """验证 retain_days 为零时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            CacheConfig(retain_days=0)
        assert "must be positive" in str(exc_info.value)

    def test_negative_retain_days_raises_error(self) -> None:
        """验证 retain_days 为负数时会报错。"""
        with pytest.raises(ValidationError):
            CacheConfig(retain_days=-1)


class TestOpsConfig:
    """OpsConfig 模型测试。"""

    def test_default_api_key(self) -> None:
        """验证 api_key 默认为空字符串。"""
        config = OpsConfig()
        assert config.api_key == ""

    def test_custom_api_key(self) -> None:
        """验证自定义 api_key。"""
        config = OpsConfig(api_key="test-key-123")
        assert config.api_key == "test-key-123"


class TestNetworkConfig:
    """全局网络配置测试。"""

    def test_default_proxy_is_none(self) -> None:
        """验证默认不启用代理。"""
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
        """验证支持的代理 URL 格式。"""
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
        """验证无效的代理 URL 会被拒绝。"""
        with pytest.raises(ValidationError):
            NetworkConfig(proxy_url=proxy_url)

    @pytest.mark.parametrize(
        "userinfo",
        ["user:proxy-secret", "user", ":proxy-secret", "user:", "", ":"],
    )
    def test_authenticated_socks_proxy_raises_safe_error(self, userinfo: str) -> None:
        """验证拒绝 SOCKS 用户信息时不会泄露凭据。"""
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
        """验证应用和脚本拒绝代理配置时不会泄露代理凭据。"""
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
    """全局 60s 公共实例配置测试。"""

    def test_defaults_disable_instance_switching(self) -> None:
        """验证空配置不会触发实例切换。"""
        config = InstancesConfig()
        assert config.list_url is None
        assert config.urls is None
        assert config.allowlist is None

    def test_normalizes_allowlist(self) -> None:
        """将纯主机名和子域名通配符统一为小写。"""
        config = InstancesConfig(allowlist=[" API.Example.COM ", "*.Example.COM"])
        assert config.allowlist == ["api.example.com", "*.example.com"]
        assert InstancesConfig(allowlist=[]).allowlist == []

    def test_allowlist_environment_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """允许通过 JSON 列表形式的环境变量提供发现主机的白名单。"""
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
        """实例匹配规则不能包含 URL 语法或无效的域名标签。"""
        with pytest.raises(ValidationError):
            InstancesConfig(allowlist=[pattern])

    def test_normalizes_urls(self) -> None:
        """验证尾部斜杠和空白条目会被移除。"""
        config = InstancesConfig(urls=["https://60s.crystelf.top/", "  ", ""])
        assert config.urls == ["https://60s.crystelf.top"]

    def test_empty_urls_become_none(self) -> None:
        """验证全部为空白的 URL 列表视为未设置。"""
        assert InstancesConfig(urls=["", "  "]).urls is None

    def test_explicit_null_urls_allowed(self) -> None:
        """验证 URL 列表允许显式设为 null。"""
        assert InstancesConfig(urls=None).urls is None
        assert InstancesConfig(list_url=None).list_url is None

    def test_rejects_unknown_keys(self) -> None:
        """验证未知配置键会被拒绝。"""
        with pytest.raises(ValidationError):
            InstancesConfig(instances=["https://60s.crystelf.top"])


class TestTemplatesConfig:
    """TemplatesConfig 模型测试。"""

    def test_get_template_by_name(self) -> None:
        """验证按名称获取模板。"""
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
        """验证未指定名称时获取默认模板。"""
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
        """验证找不到模板时会报错。"""
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
        """验证模板名称重复时会报错。"""
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
    """LoggingConfig 模型测试。"""

    def test_valid_log_level(self) -> None:
        """验证有效的日志级别。"""
        config = LoggingConfig(level="DEBUG")
        assert config.level == "DEBUG"

    def test_case_insensitive_level(self) -> None:
        """验证日志级别不区分大小写。"""
        config = LoggingConfig(level="debug")
        assert config.level == "DEBUG"

    def test_invalid_level_raises_error(self) -> None:
        """验证无效的日志级别会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(level="INVALID")
        assert "must be one of" in str(exc_info.value)


class TestTimezoneConfig:
    """TimezoneConfig 模型测试。"""

    def test_valid_iana_timezone(self) -> None:
        """验证有效的 IANA 时区。"""
        config = TimezoneConfig(business="Asia/Shanghai", display="America/New_York")
        assert config.business == "Asia/Shanghai"

    def test_display_accepts_local(self) -> None:
        """验证 display 字段接受 'local'。"""
        config = TimezoneConfig(business="Asia/Shanghai", display="local")
        assert config.display == "local"

    def test_business_rejects_local(self) -> None:
        """验证 business 字段拒绝 'local'。"""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="local", display="local")
        assert "does not accept 'local'" in str(exc_info.value)

    def test_valid_utc_offset(self) -> None:
        """验证有效的 UTC 偏移格式。"""
        config = TimezoneConfig(business="UTC+8", display="UTC-5")
        assert config.business == "UTC+8"

    def test_invalid_timezone_raises_error(self) -> None:
        """验证无效的时区会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="Invalid/Zone")
        assert "Invalid timezone" in str(exc_info.value)

    def test_utc_offset_out_of_range_raises_error(self) -> None:
        """验证 UTC 偏移超出范围时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="UTC+15")
        assert "Invalid timezone offset" in str(exc_info.value)


class TestHolidaySource:
    """HolidaySource 模型测试。"""

    def test_valid_holiday_config(self) -> None:
        """验证有效的节假日配置。"""
        config = HolidaySource(type="holiday", timeout_sec=10)
        assert config.timeout_sec == 10

    def test_obsolete_mirror_urls_are_rejected(self) -> None:
        """拒绝已移除的字段，而不是静默丢弃配置。"""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            HolidaySource.model_validate({"mirror_urls": ["https://mirror.example"]})

    def test_zero_timeout_raises_error(self) -> None:
        """验证超时时间为零时会报错。"""
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
        """验证配置缺少节假日条目时仍保持启用节假日。"""
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
    """CrazyThursdaySource 模型测试。"""

    def test_valid_config(self) -> None:
        """验证有效的疯狂星期四配置。"""
        config = CrazyThursdaySource(
            type="crazy_thursday",
            enabled=True,
            url="https://api.example.com/kfc",
            timeout_sec=5,
        )
        assert config.enabled is True

    def test_disabled_config(self) -> None:
        """验证禁用配置。"""
        config = CrazyThursdaySource(
            type="crazy_thursday",
            enabled=False,
            url="https://api.example.com/kfc",
            timeout_sec=5,
        )
        assert config.enabled is False


class TestStockIndexSource:
    """StockIndexSource 模型测试。"""

    def test_valid_config(self) -> None:
        """验证有效的股票指数配置。"""
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
        """验证对象条目会规范化代码，同时保留顺序和名称。"""
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
        """要求显式提供名称和市场，不再推断旧格式条目。"""
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
        """验证支持的对象格式会拒绝格式错误的 secid。"""
        with pytest.raises(ValidationError, match="secid must match"):
            StockIndexSource(
                type="stock_index",
                quote_url="https://api.example.com/quote",
                secids=[{"secid": secid, "name": "指数", "market": "A"}],
                market_timezones={"A": "Asia/Shanghai"},
            )

    def test_empty_secids_are_rejected(self) -> None:
        """股票数据源必须配置至少一个指数。"""
        with pytest.raises(ValidationError):
            StockIndexSource(
                quote_url="https://api.example.com/quote",
                secids=[],
                market_timezones={"A": "Asia/Shanghai"},
            )

    @pytest.mark.parametrize("missing_field", ["name", "market"])
    def test_object_items_require_name_and_market(self, missing_field: str) -> None:
        """拒绝不完整的对象，不再恢复推断出的默认值。"""
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
        """验证即使前缀不同，重复的代码也会被拒绝。"""
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
        """验证每个配置条目的市场都有对应的时区映射。"""
        with pytest.raises(ValidationError, match="must be a key of market_timezones"):
            StockIndexSource(
                type="stock_index",
                quote_url="https://api.example.com/quote",
                secids=[{"secid": "100.HSI", "name": "恒生指数", "market": "HK"}],
                market_timezones={"A": "Asia/Shanghai"},
            )


class TestTextLimitsConfig:
    """远程文本长度和新闻数量上限测试。"""

    def test_defaults(self) -> None:
        """每个文本限制都有正数默认值，以限制文本长度或数量。"""
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
        """所有字段均拒绝非正数和非整数的上限。"""
        with pytest.raises(ValidationError):
            TextLimitsConfig.model_validate({field: value})

    def test_nested_overrides_keep_other_defaults(self) -> None:
        """部分渲染配置仅覆盖指定的上限。"""
        config = TemplateRenderConfig.model_validate({"text_limits": {"news_item": 10}})
        assert config.text_limits.news_item == 10
        assert config.text_limits.news_items == 15


class TestTemplateRenderConfig:
    """TemplateRenderConfig 模型测试。"""

    def test_default_redirect_limit(self) -> None:
        """资源获取默认最多跟随五次经过校验的重定向。"""
        assert TemplateRenderConfig().remote_resource_max_redirects == 5
        assert TemplateRenderConfig(remote_resource_max_redirects=0).remote_resource_max_redirects == 0

    @pytest.mark.parametrize("limit", [-1, 21, 1.5, True, "5"])
    def test_rejects_invalid_redirect_limit(self, limit: object) -> None:
        """重定向次数上限必须是零到二十之间的整数。"""
        with pytest.raises(ValidationError):
            TemplateRenderConfig.model_validate({"remote_resource_max_redirects": limit})

    def test_default_resource_hosts(self) -> None:
        """默认仅允许现有的字体 CDN 主机。"""
        assert TemplateRenderConfig().allowed_resource_hosts == [
            "fonts.googleapis.com", "fonts.googleapis.cn",
            "fonts.gstatic.com", "fonts.gstatic.cn",
        ]

    def test_normalizes_resource_hosts(self) -> None:
        """资源主机统一为小写纯主机名；空列表禁止所有主机。"""
        assert TemplateRenderConfig(allowed_resource_hosts=[" CDN.Example.COM "]).allowed_resource_hosts == ["cdn.example.com"]
        assert TemplateRenderConfig(allowed_resource_hosts=[]).allowed_resource_hosts == []

    @pytest.mark.parametrize("hostname", [
        "", "https://example.com", "example.com/path", "example.com:443",
        "*.example.com", "user@example.com", "example.com?x=1", "example.com#x",
        "example..com", "-example.com", "example.com.", "a" * 64 + ".com",
    ])
    def test_rejects_invalid_resource_hosts(self, hostname: str) -> None:
        """资源白名单条目不能包含协议、端口或路径。"""
        with pytest.raises(ValidationError):
            TemplateRenderConfig(allowed_resource_hosts=[hostname])

    def test_valid_config(self) -> None:
        """验证有效的模板渲染配置。"""
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
        """验证设备缩放系数为零时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(device_scale_factor=0)
        assert "must be positive" in str(exc_info.value)

    def test_invalid_jpeg_quality_raises_error(self) -> None:
        """验证无效的 JPEG 质量会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(jpeg_quality=101)
        assert "must be between 1 and 100" in str(exc_info.value)

    def test_invalid_remote_resource_cache_ttl_raises_error(self) -> None:
        """验证无效的远程资源缓存 TTL 会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_cache_ttl_sec=0)
        assert "remote_resource_cache_ttl_sec must be positive" in str(exc_info.value)

    def test_invalid_remote_resource_timeout_raises_error(self) -> None:
        """验证无效的远程资源超时时间会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_timeout_sec=0)
        assert "remote_resource_timeout_sec must be positive" in str(exc_info.value)

    def test_invalid_page_load_timeout_raises_error(self) -> None:
        """验证无效的页面加载超时时间会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(page_load_timeout_sec=0)
        assert "page_load_timeout_sec must be positive" in str(exc_info.value)

    def test_font_ready_timeout_exceeds_60_raises_error(self) -> None:
        """验证字体就绪超时时间超过 60 秒时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(font_ready_timeout_sec=61.0)
        assert "font_ready_timeout_sec must not exceed 60.0" in str(exc_info.value)

    def test_max_size_kb_valid(self) -> None:
        """验证有效的远程资源大小上限。"""
        config = TemplateRenderConfig(remote_resource_max_size_kb=5120)
        assert config.remote_resource_max_size_kb == 5120

    def test_max_size_kb_zero_raises_error(self) -> None:
        """验证大小上限为零时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_max_size_kb=0)
        assert "must be between 1 and 51200" in str(exc_info.value)

    def test_max_size_kb_too_large_raises_error(self) -> None:
        """验证大小上限超过 51200 时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_max_size_kb=51201)
        assert "must be between 1 and 51200" in str(exc_info.value)

    def test_ttl_exceeds_one_year_raises_error(self) -> None:
        """验证 TTL 超过一年时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_cache_ttl_sec=365 * 24 * 60 * 60 + 1)
        assert "must not exceed 1 year" in str(exc_info.value)

    def test_timeout_exceeds_60_raises_error(self) -> None:
        """验证超时时间超过 60 秒时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(remote_resource_timeout_sec=61.0)
        assert "must not exceed 60.0" in str(exc_info.value)


class TestNewsSource:
    """NewsSource 模型测试。"""

    def test_valid_config(self) -> None:
        """验证有效的新闻数据源配置。"""
        config = NewsSource(
            type="news", url="https://api.example.com/news", params={"key": "value"}
        )
        assert config.type == "news"
        assert config.url == "https://api.example.com/news"

    def test_empty_url_raises_error(self) -> None:
        """验证 URL 为空时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            NewsSource(type="news", url="")
        assert "cannot be empty" in str(exc_info.value)


class TestFunContentSource:
    """FunContentSource 模型测试。"""

    def test_valid_config(self) -> None:
        """验证有效的趣味内容数据源配置。"""
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
        """验证端点列表为空时会报错。"""
        with pytest.raises(ValidationError) as exc_info:
            FunContentSource(type="fun_content", endpoints=[])
        assert "cannot be empty" in str(exc_info.value)
