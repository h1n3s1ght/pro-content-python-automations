from __future__ import annotations

import re
import types
from typing import Any, List, Tuple, Union, get_args, get_origin

from pydantic import BaseModel

_SPECIAL_KEY_MAP = {
    "userdata": "user_data",
    "querystring": "query_string",
}

_UNION_ORIGINS = {Union}
if getattr(types, "UnionType", None) is not None:
    _UNION_ORIGINS.add(types.UnionType)

_CAMEL_RE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RE_2 = re.compile(r"([a-z0-9])([A-Z])")


def _to_snake(name: str) -> str:
    if not name:
        return name
    name = name.replace("-", "_").replace(" ", "_")
    name = _CAMEL_RE_1.sub(r"\1_\2", name)
    name = _CAMEL_RE_2.sub(r"\1_\2", name)
    return name.lower()


def normalize_webhook_payload(value: Any, *, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            key_str = str(key).strip()
            snake = _to_snake(key_str)
            if path and path[-1] == "metadata" and snake in {"domain_name", "domainname"}:
                snake = "business_domain"
            else:
                snake = _SPECIAL_KEY_MAP.get(snake, snake)
            normalized_val = normalize_webhook_payload(val, path=path + (snake,))
            if snake in out and isinstance(out[snake], dict) and isinstance(normalized_val, dict):
                merged = dict(out[snake])
                merged.update(normalized_val)
                out[snake] = merged
            else:
                out[snake] = normalized_val
        return out
    if isinstance(value, list):
        return [normalize_webhook_payload(item) for item in value]
    return value


def collect_unknown_fields(data: Any, model_cls: type[BaseModel]) -> list[str]:
    if not isinstance(data, dict):
        return []
    unknown: list[str] = []
    fields = model_cls.model_fields
    for key, value in data.items():
        field = fields.get(key)
        if field is None:
            unknown.append(key)
            continue
        model_info = _get_model_info(field.annotation)
        if model_info is None:
            continue
        nested_model, is_list = model_info
        if is_list:
            if isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        nested_unknown = collect_unknown_fields(item, nested_model)
                        unknown.extend(_prefix(nested_unknown, f"{key}[{idx}]."))
        else:
            if isinstance(value, dict):
                nested_unknown = collect_unknown_fields(value, nested_model)
                unknown.extend(_prefix(nested_unknown, f"{key}."))
    return unknown


def _get_model_info(annotation: Any) -> Tuple[type[BaseModel], bool] | None:
    origin = get_origin(annotation)
    if origin in (list, List):
        args = get_args(annotation)
        if args:
            model_cls = _get_model_class(args[0])
            if model_cls is not None:
                return model_cls, True
        return None
    if origin is None:
        model_cls = _get_model_class(annotation)
        if model_cls is not None:
            return model_cls, False
        return None
    if origin in _UNION_ORIGINS:
        for arg in get_args(annotation):
            info = _get_model_info(arg)
            if info is not None:
                return info
    return None


def _get_model_class(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _prefix(items: list[str], prefix: str) -> list[str]:
    if not items:
        return []
    return [f"{prefix}{item}" for item in items]
