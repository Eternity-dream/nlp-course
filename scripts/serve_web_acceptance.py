"""用独立资料目录与固定供应商响应启动浏览器验收后端。"""

import argparse
import sys
from pathlib import Path

import httpx
import uvicorn

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))

from planner.conftest import TravelServices, config  # noqa: E402
from travel_planner.config import Settings  # noqa: E402
from travel_planner.main import create_app  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--missing-config", action="store_true")
    args = parser.parse_args()
    settings = Settings(
        _env_file=None,
        data_dir=args.data_dir.absolute(),
        web_dist_dir=PROJECT / "web" / "dist",
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        amap_api_key="",
        exa_api_key="",
        deepseek_api_key="",
        tavily_api_key="",
        anysearch_api_key="",
        search_allow_anonymous=False,
    )
    if not args.missing_config:
        settings = config(
            settings,
            deepseek_api_key="deepseek-acceptance-key",
            search_allow_anonymous=True,
            search_providers=settings.search_providers,
        )
    app = create_app(settings, provider_transport=httpx.MockTransport(TravelServices()))
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
