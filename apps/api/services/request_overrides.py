from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


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


def _coerce_override_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in INT_OVERRIDE_KEYS:
        return int(value)
    return float(value)


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
    @classmethod
    def from_env(cls, *, logger: Any) -> Optional["OracleRequestDefaultsLoader"]:
        user = str(os.getenv("ORACLE_PARAM_USER", "ird")).strip()
        password = str(os.getenv("ORACLE_PARAM_PASSWORD", "ird_12#$")).strip()
        dsn = str(os.getenv("ORACLE_PARAM_DSN", "(DESCRIPTION=(ADDRESS_LIST=(ADDRESS=(PROTOCOL=TCP)(HOST=172.31.234.203)(PORT=1253)))(CONNECT_DATA=(SERVICE_NAME=KNTIS)))")).strip()
        if not user or not password or not dsn:
            return None
        return cls(user=user, password=password, dsn=dsn, logger=logger)

    async def load_defaults(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._load_defaults_sync)

    def _load_defaults_sync(self) -> Dict[str, Any]:
        try:
            import oracledb  # type: ignore
        except Exception:
            self.logger.warning("[request_overrides] Oracle defaults unavailable: oracledb import failed")
            return {}

        conn = None
        cursor = None
        try:
            conn = oracledb.connect(user=self.user, password=self.password, dsn=self.dsn)
            cursor = conn.cursor()
            cursor.execute(self.query)
            row = cursor.fetchone()
            if not row:
                return {}
            column_names = [desc[0] for desc in (cursor.description or [])]
            result_row = {name: row[idx] for idx, name in enumerate(column_names)}
            return normalize_oracle_request_defaults(result_row)
        except Exception as exc:
            self.logger.warning("[request_overrides] Oracle defaults lookup failed: %s", exc)
            return {}
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
