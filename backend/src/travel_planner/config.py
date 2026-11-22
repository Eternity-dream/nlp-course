from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SEARCH_PROVIDERS = ("exa", "deepseek", "tavily", "anysearch")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    web_dist_dir: Path = Path("web/dist")
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_json_mode: bool = False
    provider_timeout_seconds: float = Field(default=30, ge=1, le=300)
    search_providers: str = ",".join(SEARCH_PROVIDERS)
    search_allow_anonymous: bool = True
    exa_api_key: SecretStr = SecretStr("")
    deepseek_api_key: SecretStr = SecretStr("")
    tavily_api_key: SecretStr = SecretStr("")
    anysearch_api_key: SecretStr = SecretStr("")
    amap_api_key: SecretStr = SecretStr("")

    def status(self) -> dict:
        missing = []
        for name in ("llm_base_url", "llm_api_key", "llm_model", "amap_api_key"):
            value = getattr(self, name)
            if not (
                value.get_secret_value().strip() if isinstance(value, SecretStr) else value.strip()
            ):
                missing.append(name.upper())
        errors = []
        if self.llm_base_url:
            try:
                url = urlsplit(self.llm_base_url)
                valid = (
                    url.scheme in {"http", "https"}
                    and url.hostname
                    and not url.username
                    and not url.password
                    and not url.query
                    and not url.fragment
                    and url.port != 0
                )
            except ValueError:
                valid = False
            if not valid:
                errors.append("LLM_BASE_URL 需要有效的 HTTP(S) 基址，凭据请单独配置")
        order = [item.strip() for item in self.search_providers.split(",") if item.strip()]
        if not order or len(order) != len(set(order)) or not set(order).issubset(SEARCH_PROVIDERS):
            errors.append(f"SEARCH_PROVIDERS 需要不重复的 {'、'.join(SEARCH_PROVIDERS)} 列表")
        search = []
        for name in order:
            if name not in SEARCH_PROVIDERS:
                continue
            key = getattr(self, f"{name}_api_key").get_secret_value().strip()
            if key:
                mode = "key"
            elif self.search_allow_anonymous and name in {"exa", "anysearch"}:
                mode = "anonymous"
            else:
                mode = "disabled"
            search.append(
                {
                    "provider": name,
                    "auth_mode": mode,
                    "configured": mode != "disabled",
                    "protocol": "mcp" if name == "exa" and mode == "anonymous" else "rest",
                }
            )
        if search and not any(provider["configured"] for provider in search):
            errors.append("至少启用一家搜索服务：填写 Key 或允许匿名访问")
        return {
            "ready_for_planning": not missing and not errors,
            "missing": missing,
            "errors": errors,
            "llm": {
                "configured": not any(x.startswith("LLM_") for x in missing)
                and not any(x.startswith("LLM_") for x in errors)
            },
            "amap": {"configured": "AMAP_API_KEY" not in missing},
            "search": search,
            "connectivity_checked": False,
            "restart_required_for_changes": True,
        }
