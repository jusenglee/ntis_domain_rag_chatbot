"""NTIS RAG FastAPI composition root."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from langchain_core.messages import HumanMessage

from apps.api.contracts.runtime_contracts import friendly_strategy_violation_message
from apps.api.rag_mapper.rag_mapper import RagMapper
from apps.api.request_overrides import OracleRequestDefaultsLoader
from apps.api.routes import RouteDeps, register_routes
from apps.api.runtime import AppRuntimeConfig, initialize_app_runtime, shutdown_app_runtime
from apps.api.runtime_helpers import (
    compute_total_ms_from_start,
    derive_stream_error_code,
    extract_contract_failure_details,
    extract_stream_chunk_text_and_field,
    is_debug_logging_enabled,
    log_event,
    logger,
    mask_query_for_log,
)
from apps.planner.planner_defaults import (
    PLANNER_STAGE1_PROMPT_VERSION,
    PLANNER_STAGE15_PROMPT_VERSION,
    PLANNER_STAGE2_PROMPT_VERSION,
    PLANNER_STAGEWISE_ENABLED,
)
from apps.planner.planner_contract import StrategyViolation
from apps.platform.metrics import (
    PROMETHEUS_TIMEOUT as METRICS_PROMETHEUS_TIMEOUT,
    STREAM_INTERVAL_SECONDS as METRICS_STREAM_INTERVAL_SECONDS,
    collect_snapshot as collect_metrics_snapshot,
)
from apps.platform.settings import REDIS_URL
from apps.retrieval.rag_retriever import is_hit_source
from apps.retrieval.rag_runtime_observability import set_log_context

REQUEST_DEFAULTS_LOADER = OracleRequestDefaultsLoader.from_env(logger=logger)
TEMPLATE_INDEX_PATH = Path("templates/index.html")

_RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT = str(os.getenv("RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT", "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

APP_RUNTIME_CONFIG = AppRuntimeConfig(
    redis_url=REDIS_URL,
    file_kv_root=str(os.getenv("FILE_KVSTORE_ROOT", "local_kvstore") or "local_kvstore").strip() or "local_kvstore",
    planner_stagewise_enabled=PLANNER_STAGEWISE_ENABLED,
    planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
    planner_stage15_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
    planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
    ensure_payload_index_on_boot=_RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT,
    sparse_warmup_on_boot=str(os.getenv("RAG_FASTEMBED_WARMUP_ON_BOOT", "true")).strip().lower() in {"1", "true", "yes", "on"},
    metrics_timeout_seconds=METRICS_PROMETHEUS_TIMEOUT,
    payload_keyword_index_targets={
        "ntis_project_v1": ["pjt_id", "pjt_no", "tag", "prtcp_mp[].hm_id", "doc_id", "prtcp_mp[].hm_nm"],
        "ntis_perf_v1": ["pjt_id", "pjt_no", "tag", "prtcp_mp[].hm_id", "doc_id", "prtcp_mp[].hm_nm", "rst_id"],
    },
    payload_optional_keyword_index_targets={
        "ntis_project_v1": ["prtcp_mp[].gndr_slct_nm"],
        "ntis_perf_v1": ["perf_id", "paper_id", "prtcp_mp[].gndr_slct_nm"],
    },
    payload_text_index_targets={
        "ntis_project_v1": ["org_nm", "prtcp_org[].org_nm", "prtcp_mp[].blng_org_nm", "title_text", "title1", "title2"],
        "ntis_perf_v1": ["org_nm", "prtcp_org[].org_nm", "prtcp_mp[].blng_org_nm", "title_text", "title1", "title2"],
    },
    payload_integer_index_targets={
        "ntis_project_v1": ["stan_yr", "meta_basic.stan_yr"],
        "ntis_perf_v1": ["stan_yr", "meta_basic.stan_yr"],
    },
    payload_datetime_index_targets={
        "ntis_project_v1": ["dt1", "dt2", "meta_basic.tot_rsch_start_dt", "meta_basic.tot_rsch_end_dt"],
        "ntis_perf_v1": ["dt1", "dt2", "meta_basic.tot_rsch_start_dt", "meta_basic.tot_rsch_end_dt"],
    },
)


def create_app() -> FastAPI:
    """Create the FastAPI app and wire runtime lifecycle plus routes."""

    @asynccontextmanager
    async def runtime_lifespan(app: FastAPI):
        try:
            await initialize_app_runtime(app, config=APP_RUNTIME_CONFIG)
            yield
        finally:
            await shutdown_app_runtime(app)

    app = FastAPI(lifespan=runtime_lifespan)
    register_routes(
        app,
        RouteDeps(
            template_index_path=TEMPLATE_INDEX_PATH,
            logger=logger,
            log_event=log_event,
            is_debug_logging_enabled=is_debug_logging_enabled,
            mask_query_for_log=mask_query_for_log,
            extract_stream_chunk_text_and_field=extract_stream_chunk_text_and_field,
            is_hit_source=is_hit_source,
            derive_stream_error_code=derive_stream_error_code,
            compute_total_ms_from_start=compute_total_ms_from_start,
            extract_contract_failure_details=extract_contract_failure_details,
            friendly_strategy_violation_message=friendly_strategy_violation_message,
            collect_metrics_snapshot=collect_metrics_snapshot,
            metrics_stream_interval_seconds=METRICS_STREAM_INTERVAL_SECONDS,
            set_log_context=set_log_context,
            rag_mapper=RagMapper,
            human_message=HumanMessage,
            strategy_violation=StrategyViolation,
            request_defaults_loader=REQUEST_DEFAULTS_LOADER,
        ),
    )
    return app
