"""The seller profile (what "we" sell, ICP, tone, personas...). Spec: seller_profile.yaml."""

from functools import lru_cache
from typing import Any

import yaml

from app.config import settings


@lru_cache(maxsize=1)
def load_seller_profile() -> dict[str, Any]:
    return yaml.safe_load(settings.seller_profile_path.read_text())
