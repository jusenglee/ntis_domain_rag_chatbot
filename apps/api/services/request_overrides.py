from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


ORACLE_REQUEST_PARAM_TABLE = "IRD_PARAM"
ORACLE_REQUEST_PARAM_QUERY = """
SELECT
    MAX(CASE WHEN PARAM_NM = 'temperature' THEN TO_NUMBER(PARAM_VAL) END) AS temperature,
    MAX(CASE WHEN PARAM_NM = 'topP' THEN TO_NUMBER(PARAM_VAL) END) AS topP,
    MAX(CASE WHEN PARAM_NM = 'topK' THEN TO_NUMBER(PARAM_VAL) END) AS topK,
    MAX(CASE WHEN PARAM_NM = 'maxTokens' THEN TO_NUMBER(PARAM_VAL) END) AS maxTokens,
    MAX(CASE WHEN PARAM_NM = 'ragMinDenseScore' THEN TO_NUMBER(PARAM_VAL) END) AS ragMinDenseScore,
    MAX(CASE WHEN PARAM_NM = 'ragWeightLexical' THEN TO_NUMBER(PARAM_VAL) END) AS ragWeightLexical,
    MAX(CASE WHEN PARAM_NM = 'ragTopKDense' THEN TO_NUMBER(PARAM_VAL) END) AS ragTopKDense,
    MAX(CASE WHEN PARAM_NM = 'ragTopKLexicalCandidate' THEN TO_NUMBER(PARAM_VAL) END) AS ragTopKLexicalCandidate
FROM IRD_PARAM
""".strip()


ORACLE_TO_REQUEST_OVERRIDE_KEY = {
    "temperature": "temperature",
    "topP": "top_p",
    "topK": "top_k",
    "maxTokens": "max_tokens",
    "ragMinDenseScore": "RAG_MIN_DENSE_SCORE",
    "ragWeightLexical": "RAG_W_LEX",
    "ragTopKDense": "RAG_TOPK_DENSE",
    "ragTopKLexicalCandidate": "RAG_TOPK_LEX_CAND",
}

INT_OVERRIDE_KEYS = {"top_k", "max_tokens", "RAG_TOPK_DENSE", "RAG_TOPK_LEX_CAND"}
_DSN_FIELD_PATTERNS = {
    "host": re.compile(r"\(HOST=([^)]+)\)", re.IGNORECASE),
    "port": re.compile(r"\(PORT=([^)]+)\)", re.IGNORECASE),
    "service_name": re.compile(r"\(SERVICE_NAME=([^)]+)\)", re.IGNORECASE),
}


def _coerce_override_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in INT_OVERRIDE_KEYS:
        return int(value)
    return float(value)


def _extract_dsn_field(dsn: str, field: str) -> Optional[str]:
    pattern = _DSN_FIELD_PATTERNS.get(field)
    if pattern is None:
        return None
    match = pattern.search(str(dsn or ""))
    if not match:
        return None
    value = str(match.group(1) or "").strip()
    return value or None


def merge_request_overrides(
    *,
    request_values: Mapping[str, Any],
    oracle_defaults: Optional[Mapping[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, str]]:
    merged: Dict[str, Any] = {}
    sources: Dict[str, str] = {}

    for source_name, values in (("oracle", dict(oracle_defaults or {})), ("request", dict(request_values or {}))):
        for key, value in values.items():
            if value is None:
                continue
            merged[key] = value
            sources[key] = source_name
    return merged, sources


def normalize_oracle_request_defaults(row: Mapping[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for oracle_key, request_key in ORACLE_TO_REQUEST_OVERRIDE_KEY.items():
        value = row.get(oracle_key)
        if value is None:
            continue
        try:
            normalized[request_key] = _coerce_override_value(request_key, value)
        except Exception:
            continue
    return normalized


@dataclass(frozen=True)
class OracleRequestDefaultsLoader:
    user: str
    password: str
    dsn: str
    logger: Any
    query: str = ORACLE_REQUEST_PARAM_QUERY

    def _log_info(self, message: str, *args: Any) -> None:
        log_fn = getattr(self.logger, "info", None)
        if callable(log_fn):
            log_fn(message, *args)

    def _log_warning(self, message: str, *args: Any) -> None:
        log_fn = getattr(self.logger, "warning", None)
        if callable(log_fn):
            log_fn(message, *args)

    def connector_summary(self) -> Dict[str, Any]:
        return {
            "oracle_connector_user": self.user,
            "oracle_connector_host": _extract_dsn_field(self.dsn, "host"),
            "oracle_connector_port": _extract_dsn_field(self.dsn, "port"),
            "oracle_connector_service_name": _extract_dsn_field(self.dsn, "service_name"),
            "oracle_connector_table": ORACLE_REQUEST_PARAM_TABLE,
        }

    @classmethod
    def from_env(cls, *, logger: Any) -> Optional["OracleRequestDefaultsLoader"]:
        user = str(os.getenv("ORACLE_PARAM_USER", "ird")).strip()
        password = str(os.getenv("ORACLE_PARAM_PASSWORD", "ird_12#$")).strip()
        dsn = str(os.getenv("ORACLE_PARAM_DSN", "(DESCRIPTION=(ADDRESS_LIST=(ADDRESS=(PROTOCOL=TCP)(HOST=172.31.234.203)(PORT=1253)))(CONNECT_DATA=(SERVICE_NAME=KNTIS)))")).strip()
        if not user or not password or not dsn:
            return None
        loader = cls(user=user, password=password, dsn=dsn, logger=logger)
        summary = loader.connector_summary()
        loader._log_info(
            "[request_overrides] Oracle defaults connector configured: user=%s host=%s port=%s service_name=%s table=%s",
            summary.get("oracle_connector_user"),
            summary.get("oracle_connector_host"),
            summary.get("oracle_connector_port"),
            summary.get("oracle_connector_service_name"),
            summary.get("oracle_connector_table"),
        )
        return loader

    async def load_defaults(self) -> Dict[str, Any]:
        defaults, _ = await self.load_defaults_with_meta()
        return defaults

    async def load_defaults_with_meta(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        return await asyncio.to_thread(self._load_defaults_sync_with_meta)

    def _load_defaults_sync(self) -> Dict[str, Any]:
        defaults, _ = self._load_defaults_sync_with_meta()
        return defaults

    def _load_defaults_sync_with_meta(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        meta: Dict[str, Any] = {
            **self.connector_summary(),
            "oracle_lookup_attempted": False,
            "oracle_lookup_status": "not_started",
            "oracle_loaded_key_count": 0,
            "oracle_loaded_keys": [],
        }
        try:
            import oracledb  # type: ignore
        except Exception as exc:
            meta["oracle_lookup_status"] = "import_failed"
            meta["oracle_lookup_error"] = str(exc)
            self._log_warning("[request_overrides] Oracle defaults unavailable: oracledb import failed")
            return {}, meta

        conn = None
        cursor = None
        try:
            meta["oracle_lookup_attempted"] = True
            conn = oracledb.connect(user=self.user, password=self.password, dsn=self.dsn)
            cursor = conn.cursor()
            cursor.execute(self.query)
            row = cursor.fetchone()
            if not row:
                meta["oracle_lookup_status"] = "empty"
                return {}, meta
            column_names = [desc[0] for desc in (cursor.description or [])]
            result_row = {name: row[idx] for idx, name in enumerate(column_names)}
            normalized = normalize_oracle_request_defaults(result_row)
            meta["oracle_lookup_status"] = "loaded" if normalized else "empty"
            meta["oracle_loaded_key_count"] = len(normalized)
            meta["oracle_loaded_keys"] = sorted(normalized.keys())
            return normalized, meta
        except Exception as exc:
            meta["oracle_lookup_status"] = "query_failed"
            meta["oracle_lookup_error"] = str(exc)
            self._log_warning("[request_overrides] Oracle defaults lookup failed: %s", exc)
            return {}, meta
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
