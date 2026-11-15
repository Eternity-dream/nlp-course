import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from travel_planner.providers.errors import ProviderError
from travel_planner.providers.models import SearchSource


async def fetch_local(source: SearchSource) -> SearchSource:
    """复用知识库的公网下载和隔离解析，下载内容只在本次调用保留。"""
    with tempfile.TemporaryDirectory(prefix="travel-fetch-") as directory:
        root = Path(directory)
        input_path, output_path, raw_path = root / "url", root / "result", root / "raw"
        input_path.write_text(source.url, encoding="utf-8")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "travel_planner.knowledge.parse",
            "url",
            str(input_path),
            str(output_path),
            str(raw_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(30):
                await process.wait()
            if process.returncode != 0:
                raise ProviderError("local", "source_unavailable")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            if "error" in result:
                raise ProviderError("local", "source_unavailable")
            text = "\n".join(part["text"] for part in result["segments"])[:50000]
            if not text.strip():
                raise ProviderError("local", "source_unavailable")
            return source.model_copy(
                update={"provider": "local", "text": text, "fetched_at": datetime.now(UTC)}
            )
        except TimeoutError as exc:
            raise ProviderError("local", "timeout") from exc
        except (ValueError, KeyError, OSError) as exc:
            raise ProviderError("local", "source_unavailable") from exc
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
