# rag_pipeline/settings.py
import os
import logging
from pathlib import Path

# 로깅
logger = logging.getLogger("RAG_Pipeline")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("RAG_Pipeline")

# Qdrant / Embedding (A)
QDRANT_HOST  = os.getenv("QDRANT_HOST", "203.250.234.159")
QDRANT_PORT  = int(os.getenv("QDRANT_PORT", 8005))
EMBED_MODEL  = os.getenv("EMBEDDING_MODEL", "./Models/multilingual-e5-large-instruct")

# Qdrant / Embedding (B)
QDRANT_HOST_B  = os.getenv("QDRANT_HOST_B", QDRANT_HOST)
QDRANT_PORT_B  = int(os.getenv("QDRANT_PORT_B", QDRANT_PORT))
EMBED_MODEL_B  = os.getenv("EMBEDDING_MODEL_B", "./Models/multilingual-e5-large")


def _split_csv(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    value = value.strip()
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


RAG_COLLECTION_ALLOWLIST = _split_csv(
    os.getenv("RAG_COLLECTION_ALLOWLIST"),
    ["ntis_project_v1", "ntis_perf_v1"],
)

# Triton
TRITON_URL         = os.getenv("TRITON_URL", "203.250.234.159:8001")
DEFAULT_MODEL_NAME = os.getenv("TRITON_MODEL", "gpt_oss_triton_0")
TOKENIZER_MAP = {
    "gpt_oss_triton_0": os.getenv("GPT_OSS_TOKENIZER", "./Models/gpt-oss-20b"),
    "gemma_triton_0": "./Models/gemma-3-27b-it",
}

# 하이퍼파라미터
TOP_K_BASE       = 300
TOP_K_RETURN     = 20
DEFAULT_MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))
TEMPERATURE      = 0.2
TOP_P            = 0.8
SCORE_THRESHOLD  = 0.20
FUZZ_MIN         = 55
SNIPPET_MAX_CHARS = 8000
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "24000"))
MAX_DOC_SENTENCES = int(os.getenv("MAX_DOC_SENTENCES", "12"))
MAX_DOC_TOKENS = int(os.getenv("MAX_DOC_TOKENS", "800"))
SUMMARY_DOC_SENTENCES = int(os.getenv("SUMMARY_DOC_SENTENCES", "6"))
SUMMARY_DOC_TOKENS = int(os.getenv("SUMMARY_DOC_TOKENS", "240"))

# Redis / 검색 제한 설정
# REDIS_URL: Redis 연결 문자열 (예: redis://localhost:6379)
# REDIS_TTL: Redis 캐시 TTL(초)
# MAX_TOP_K_SIZE: 검색/응답에 사용할 최대 문서 수
REDIS_URL = os.getenv("REDIS_URL", "redis://redis8:6379")
REDIS_TTL = int(os.getenv("REDIS_TTL", "3600"))
MAX_TOP_K_SIZE = int(os.getenv("MAX_TOP_K_SIZE", "20"))

PROMPT_OVERHEAD_TOKENS = int(os.getenv("RAG_PROMPT_OVERHEAD_TOKENS", "900"))
CTX_SAFETY_MARGIN = int(os.getenv("RAG_CTX_SAFETY_MARGIN", "256"))
CTX_MIN_BUDGET = int(os.getenv("RAG_CTX_MIN_BUDGET", "512"))
DEFAULT_MAX_MODEL_LEN = int(os.getenv("DEFAULT_MAX_MODEL_LEN", "32768"))

def get_ctx_token_budget(model_name: str, *, max_output_tokens: int | None = None) -> int:
    """
    모델별 '문서 삽입(컨텍스트) 토큰 예산' 계산.
    """
    model_ctx = MODEL_MAX_CONTEXT.get(model_name, DEFAULT_MAX_MODEL_LEN)

    if max_output_tokens is None:
        max_output_tokens = MAX_TOKENS.get(model_name, DEFAULT_MAX_TOKENS)

    avail = model_ctx - PROMPT_OVERHEAD_TOKENS - int(max_output_tokens) - CTX_SAFETY_MARGIN

    budget = int(avail)
    logger.debug("CTX token budget calculated: %s", budget)
    return max(int(CTX_MIN_BUDGET), budget)

MODEL_MAX_CONTEXT = {
    "gpt_oss_triton_0": int(os.getenv("GPT_OSS_MAX_MODEL_LEN", str(DEFAULT_MAX_MODEL_LEN))),
    "gemma_triton_0": int(os.getenv("GEMMA_MAX_MODEL_LEN", str(DEFAULT_MAX_MODEL_LEN))),
}
MAX_TOKENS = {
    "gpt_oss_triton_0": int(os.getenv("GPT_OSS_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
    "gemma_triton_0": int(os.getenv("GEMMA_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
}


def get_model_max_output_tokens(model_name: str) -> int:
    return int(MAX_TOKENS.get(model_name, DEFAULT_MAX_TOKENS))


def _get_timeout_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _get_model_timeout_pair(
    *,
    model_env_prefix: str,
    request_type: str,
    default_first: int,
    default_idle: int,
    deprecated_env_prefix: str | None = None,
) -> tuple[int, int]:
    request_env_prefix = f"TRITON_{model_env_prefix}_{request_type}"

    first = _get_timeout_env(f"{request_env_prefix}_TIMEOUT_FIRST", default_first)
    idle = _get_timeout_env(f"{request_env_prefix}_TIMEOUT_IDLE", default_idle)

    if deprecated_env_prefix:
        deprecated_first = os.getenv(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_FIRST")
        deprecated_idle = os.getenv(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_IDLE")
        if deprecated_first is not None:
            logger.warning(
                "[DEPRECATED] %s_%s_TIMEOUT_FIRST is deprecated and will be removed in v0.5.0. "
                "Use %s_TIMEOUT_FIRST instead.",
                deprecated_env_prefix,
                request_type,
                request_env_prefix,
            )
            first = int(deprecated_first)
        if deprecated_idle is not None:
            logger.warning(
                "[DEPRECATED] %s_%s_TIMEOUT_IDLE is deprecated and will be removed in v0.5.0. "
                "Use %s_TIMEOUT_IDLE instead.",
                deprecated_env_prefix,
                request_type,
                request_env_prefix,
            )
            idle = int(deprecated_idle)

    return first, idle



GPT_OSS_STREAM_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GPT_OSS_TRITON_0",
    request_type="STREAM",
    default_first=4,
    default_idle=20,
    deprecated_env_prefix="GPT_OSS",
)
GPT_OSS_SYNC_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GPT_OSS_TRITON_0",
    request_type="SYNC",
    default_first=GPT_OSS_STREAM_TIMEOUTS[0],
    default_idle=GPT_OSS_STREAM_TIMEOUTS[1],
    deprecated_env_prefix="GPT_OSS",
)

GEMMA_STREAM_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GEMMA_TRITON_0",
    request_type="STREAM",
    default_first=4,
    default_idle=20,
    deprecated_env_prefix="GEMMA",
)
GEMMA_SYNC_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GEMMA_TRITON_0",
    request_type="SYNC",
    default_first=GEMMA_STREAM_TIMEOUTS[0],
    default_idle=GEMMA_STREAM_TIMEOUTS[1],
    deprecated_env_prefix="GEMMA",
)

TRITON_TIMEOUTS = {
    "gpt_oss_triton_0": {
        "stream": GPT_OSS_STREAM_TIMEOUTS,
        "sync": GPT_OSS_SYNC_TIMEOUTS,
    },
    "gemma_triton_0": {
        "stream": GEMMA_STREAM_TIMEOUTS,
        "sync": GEMMA_SYNC_TIMEOUTS,
    },
}

# 벤치 로그
LOG_DIR = Path(os.getenv("RAG_BENCH_LOG_DIR", "./rag_bench_logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault(
    "CTX_TOKEN_BUDGET",
    str(
        get_ctx_token_budget(
            DEFAULT_MODEL_NAME,
            max_output_tokens=get_model_max_output_tokens(DEFAULT_MODEL_NAME),
        )
    ),
)
os.environ.setdefault("SNIPPET_MAX_CHARS", str(SNIPPET_MAX_CHARS))
