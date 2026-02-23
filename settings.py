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
QDRANT_HOST  = os.getenv("QDRANT_HOST", "qdrant-ntis3")
QDRANT_PORT  = int(os.getenv("QDRANT_PORT", 6334))
EMBED_MODEL  = os.getenv("EMBEDDING_MODEL", "../../Models/multilingual-e5-large-instruct")

# Qdrant / Embedding (B)
QDRANT_HOST_B  = os.getenv("QDRANT_HOST_B", QDRANT_HOST)
QDRANT_PORT_B  = int(os.getenv("QDRANT_PORT_B", QDRANT_PORT))
EMBED_MODEL_B  = os.getenv("EMBEDDING_MODEL_B", "../../Models/multilingual-e5-large")



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
TRITON_URL         = os.getenv("TRITON_URL", "triton_ntis3:8001")
DEFAULT_MODEL_NAME = os.getenv("TRITON_MODEL", "gpt_oss_triton_0")
TOKENIZER_MAP = {
    "gpt_oss_triton_0": "../../Models/gpt-oss-120b",
    "gemma_triton_0": "../../Models/gemma-3-27b-it",
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


GPT_OSS_STREAM_TIMEOUT_FIRST = _get_timeout_env("GPT_OSS_STREAM_TIMEOUT_FIRST", 8)
GPT_OSS_STREAM_TIMEOUT_IDLE = _get_timeout_env("GPT_OSS_STREAM_TIMEOUT_IDLE", 60)
GEMMA_STREAM_TIMEOUT_FIRST = _get_timeout_env("GEMMA_STREAM_TIMEOUT_FIRST", 4)
GEMMA_STREAM_TIMEOUT_IDLE = _get_timeout_env("GEMMA_STREAM_TIMEOUT_IDLE", 20)

GPT_OSS_SYNC_TIMEOUT_FIRST = _get_timeout_env(
    "GPT_OSS_SYNC_TIMEOUT_FIRST",
    GPT_OSS_STREAM_TIMEOUT_FIRST,
)
GPT_OSS_SYNC_TIMEOUT_IDLE = _get_timeout_env(
    "GPT_OSS_SYNC_TIMEOUT_IDLE",
    GPT_OSS_STREAM_TIMEOUT_IDLE,
)
GEMMA_SYNC_TIMEOUT_FIRST = _get_timeout_env(
    "GEMMA_SYNC_TIMEOUT_FIRST",
    GEMMA_STREAM_TIMEOUT_FIRST,
)
GEMMA_SYNC_TIMEOUT_IDLE = _get_timeout_env(
    "GEMMA_SYNC_TIMEOUT_IDLE",
    GEMMA_STREAM_TIMEOUT_IDLE,
)

TRITON_TIMEOUTS = {
    "gpt_oss_triton_0": {
        "stream": (GPT_OSS_STREAM_TIMEOUT_FIRST, GPT_OSS_STREAM_TIMEOUT_IDLE),
        "sync": (GPT_OSS_SYNC_TIMEOUT_FIRST, GPT_OSS_SYNC_TIMEOUT_IDLE),
    },
    "gemma_triton_0": {
        "stream": (GEMMA_STREAM_TIMEOUT_FIRST, GEMMA_STREAM_TIMEOUT_IDLE),
        "sync": (GEMMA_SYNC_TIMEOUT_FIRST, GEMMA_SYNC_TIMEOUT_IDLE),
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
