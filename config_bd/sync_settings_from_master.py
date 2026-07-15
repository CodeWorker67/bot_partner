"""
Синхронизация identity-полей через API (серверы разные, прямой доступ к PG не нужен).

Рекомендуемый способ — на VPS партнёров вызвать partner_api:

  curl -s -X POST "http://127.0.0.1:8090/bots/settings/pull-from-master" \\
    -H "X-Api-Key: $PARTNER_VPS_API_KEY" \\
    -H "Content-Type: application/json" \\
    -d '{"dry_run": true}'

  curl -s -X POST "http://127.0.0.1:8090/bots/settings/pull-from-master" \\
    -H "X-Api-Key: $PARTNER_VPS_API_KEY" \\
    -H "Content-Type: application/json" \\
    -d '{"dry_run": false}'

В .env partner_api должны быть:
  MASTER_BOT_API_URL=https://...
  MASTER_BOT_API_KEY=<тот же, что PARTNER_BOT_API_KEY на мастере>

Альтернатива с мастера (push):
  cd Zoomer && python -m config_bd.push_partner_settings_to_vps
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def _partner_api_base() -> str:
    base = (
        os.environ.get("PARTNER_API_URL")
        or os.environ.get("PARTNER_VPS_IP")
        or "http://127.0.0.1:8090"
    ).strip().rstrip("/")
    return base


def _partner_api_key() -> str:
    key = (os.environ.get("PARTNER_VPS_API_KEY") or "").strip()
    if not key:
        raise SystemExit("Задайте PARTNER_VPS_API_KEY (ключ partner_api).")
    return key


def _post_json(url: str, body: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise SystemExit(f"Не удалось достучаться до partner_api: {e}") from e


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull identity settings via partner_api ← master API"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--api-url",
        default=None,
        help="Базовый URL partner_api (по умолчанию PARTNER_API_URL или http://127.0.0.1:8090)",
    )
    args = parser.parse_args()

    base = (args.api_url or _partner_api_base()).rstrip("/")
    url = f"{base}/bots/settings/pull-from-master"
    result = _post_json(
        url,
        {"dry_run": args.dry_run, "only_existing": True},
        _partner_api_key(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
