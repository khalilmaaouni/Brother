"""OpenRouter model pricing catalog helpers."""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from scripts.loop import proof_ledger


DEFAULT_MAX_AGE_DAYS = 7.0


class CatalogError(Exception):
    """Raised when the OpenRouter catalog cannot be read or is malformed."""


@dataclass(frozen=True)
class ModelPricing:
    prompt: Any
    completion: Any
    max_completion_tokens: Optional[int] = None


@dataclass(frozen=True)
class Catalog:
    path: str
    models: Mapping[str, ModelPricing]
    raw: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        if key in self.models:
            return self.models[key]
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.models:
            return self.models[key]
        return self.raw.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.models or key in self.raw


_USAGE_RE = re.compile(
    r"^\[usage\]\s+(?:prompt=(\d+)\s+completion=(\d+)|input=(\d+)\s+output=(\d+))\s+model=(\S+)\s*$"
)


def _build_models(data: Any) -> Dict[str, ModelPricing]:
    if not isinstance(data, list):
        raise CatalogError("catalog data must be a list")
    models: Dict[str, ModelPricing] = {}
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise CatalogError(f"catalog data[{index}] must be an object")
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise CatalogError(f"catalog data[{index}] has no valid id")
        pricing = item.get("pricing")
        prompt_price = None
        completion_price = None
        if isinstance(pricing, dict):
            prompt_price = pricing.get("prompt")
            completion_price = pricing.get("completion")
        top_provider = item.get("top_provider")
        max_completion_tokens = None
        if isinstance(top_provider, dict):
            max_tokens = top_provider.get("max_completion_tokens")
            if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
                max_completion_tokens = max_tokens
        models[model_id] = ModelPricing(
            prompt=prompt_price,
            completion=completion_price,
            max_completion_tokens=max_completion_tokens,
        )
    return models


def load_catalog(path: str) -> Catalog:
    """Load a saved OpenRouter models endpoint JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = proof_ledger.loads(handle.read())   # a repeated member (a price of 9 then 0) refuses
    except OSError as exc:
        raise CatalogError(f"catalog file could not be read: {path}") from exc
    except ValueError as exc:
        raise CatalogError(f"catalog file is not valid JSON: {path}") from exc

    if not isinstance(raw, dict):
        raise CatalogError("catalog root must be an object")
    models = _build_models(raw.get("data"))
    return Catalog(path=str(path), models=models, raw=raw)


def catalog_age_days(path: str, now: Optional[float] = None) -> float:
    """Return catalog age in days based on file modification time."""
    if now is None:
        now = time.time()
    try:
        mtime = os.path.getmtime(path)
    except OSError as exc:
        raise CatalogError(f"catalog file is missing: {path}") from exc
    return (float(now) - float(mtime)) / 86400.0


def parse_usage(stderr_text: str) -> Optional[Tuple[int, int, str]]:
    """Parse the last line-anchored usage line from stderr text."""
    if not isinstance(stderr_text, str):
        return None

    last_line: Optional[str] = None
    for line in stderr_text.splitlines():
        if line.startswith("[usage]"):
            last_line = line

    if last_line is None:
        return None

    match = _USAGE_RE.match(last_line)
    if match is None:
        return None

    prompt = match.group(1)
    completion = match.group(2)
    if prompt is None or completion is None:
        prompt = match.group(3)
        completion = match.group(4)
    model = match.group(5)

    if prompt is None or completion is None or model is None:
        return None

    try:
        prompt_tokens = int(prompt)
        completion_tokens = int(completion)
    except ValueError:
        return None   # sbe: allow-silent None is no usage line: measured_cost reads the call unmeasured and it settles ABANDONED, never zero

    return (prompt_tokens, completion_tokens, model)


def _parse_price(value: Any) -> Tuple[Optional[float], Optional[str]]:
    if value is None:
        return None, "missing"
    if isinstance(value, bool):
        return None, "non-numeric"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None, "non-numeric"
    if not math.isfinite(price):
        return None, "non-finite"
    if price < 0:
        return None, "negative"
    return price, None


def _validate_tokens(value: Any, name: str) -> Tuple[Optional[int], Optional[str]]:
    if value is None:
        return None, f"{name} tokens are None"
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"invalid {name} tokens"
    if value < 0:
        return None, f"invalid {name} tokens"
    return value, None


def _pricing_value(pricing: Any, key: str) -> Any:
    if isinstance(pricing, dict):
        return pricing.get(key)
    return getattr(pricing, key, None)


def _models_from_raw(raw: Mapping[str, Any]) -> Optional[Mapping[str, ModelPricing]]:
    data = raw.get("data")
    if not isinstance(data, list):
        return None
    try:
        return _build_models(data)
    except CatalogError:
        return None   # sbe: allow-silent None is no catalog: cost() answers unpriced, so nothing is bound or measured from it


def cost(
    catalog: Any,
    model: str,
    prompt_tokens: Any,
    completion_tokens: Any,
    max_age_days: Optional[float] = DEFAULT_MAX_AGE_DAYS,
) -> Tuple[Optional[float], Optional[str]]:
    """Return (usd, None) for a measured cost, or (None, reason) for no data."""
    if catalog is None:
        return None, "no catalog"
    if not isinstance(model, str) or not model:
        return None, "model absent"

    if prompt_tokens is None or completion_tokens is None:
        return None, "tokens unmeasured"

    prompt_tokens, prompt_error = _validate_tokens(prompt_tokens, "prompt")
    if prompt_error is not None:
        return None, prompt_error

    completion_tokens, completion_error = _validate_tokens(completion_tokens, "completion")
    if completion_error is not None:
        return None, completion_error

    catalog_path = getattr(catalog, "path", None)
    if catalog_path is None and isinstance(catalog, dict):
        catalog_path = catalog.get("__path__")
    if catalog_path is not None and max_age_days is not None:
        try:
            age = catalog_age_days(catalog_path, None)
        except CatalogError:
            return None, "catalog missing"
        if age > max_age_days:
            return None, "stale catalog"

    models = getattr(catalog, "models", None)
    if models is None and isinstance(catalog, dict):
        models = _models_from_raw(catalog)
    if models is None:
        return None, "no catalog"

    pricing = models.get(model)
    if pricing is None:
        return None, "model absent"

    prompt_price, prompt_reason = _parse_price(_pricing_value(pricing, "prompt"))
    if prompt_reason is not None:
        return None, f"prompt price {prompt_reason}"

    completion_price, completion_reason = _parse_price(_pricing_value(pricing, "completion"))
    if completion_reason is not None:
        return None, f"completion price {completion_reason}"

    total = (prompt_tokens * prompt_price) + (completion_tokens * completion_price)
    if not math.isfinite(total) or total < 0:
        return None, "invalid total cost"
    return total, None
