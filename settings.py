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
EMBED_MODEL  = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large-instruct")

# Qdrant / Embedding (B)
QDRANT_HOST_B  = os.getenv("QDRANT_HOST_B", QDRANT_HOST)
QDRANT_PORT_B  = int(os.getenv("QDRANT_PORT_B", QDRANT_PORT))
EMBED_MODEL_B  = os.getenv("EMBEDDING_MODEL_B", "intfloat/multilingual-e5-large")


# Triton
TRITON_URL         = os.getenv("TRITON_URL", "http://203.250.234.159:8001")
DEFAULT_MODEL_NAME = os.getenv("TRITON_MODEL", "gemma_vllm_0")
TOKENIZER_MAP = {
    # "gpt_oss_0": "../../Models/gpt-oss-120b",
    # "gemma_vllm_0": "../../Models/gemma-3-27b-it",
}

# 하이퍼파라미터
TOP_K_BASE       = 300
TOP_K_RETURN     = 20
MAX_TOKENS = 16000
CTX_TOKEN_BUDGET = 16000
TEMPERATURE      = 0.2
TOP_P            = 0.8
SCORE_THRESHOLD  = 0.20
FUZZ_MIN         = 55
SNIPPET_MAX_CHARS = 8000

PROMPT_OVERHEAD_TOKENS = int(os.getenv("RAG_PROMPT_OVERHEAD_TOKENS", "900"))
CTX_SAFETY_MARGIN = int(os.getenv("RAG_CTX_SAFETY_MARGIN", "256"))
CTX_MIN_BUDGET = int(os.getenv("RAG_CTX_MIN_BUDGET", "512"))

def get_ctx_token_budget(model_name: str, *, max_output_tokens: int | None = None) -> int:
    """
    모델별 '문서 삽입(컨텍스트) 토큰 예산' 계산.
    """
    model_ctx = MODEL_MAX_CONTEXT.get(model_name) or int(os.getenv("DEFAULT_MAX_MODEL_LEN", "8192"))

    if max_output_tokens is None:
        max_output_tokens = MODEL_MAX_OUTPUT_TOKENS.get(model_name, MAX_TOKENS)

    avail = model_ctx - PROMPT_OVERHEAD_TOKENS - int(max_output_tokens) - CTX_SAFETY_MARGIN

    budget = int(avail)
    logger.info(budget)
    return max(int(CTX_MIN_BUDGET), budget)

MODEL_MAX_CONTEXT = {
    "gpt_oss_0": int(os.getenv("GPT_OSS_MAX_MODEL_LEN", "65536")),
    "gemma_vllm_0": int(os.getenv("GEMMA_MAX_MODEL_LEN", "65536")),
}
MODEL_MAX_OUTPUT_TOKENS = {
    "gpt_oss_0": int(os.getenv("GPT_OSS_MAX_TOKENS", str(MAX_TOKENS))),
    "gemma_vllm_0": int(os.getenv("GEMMA_MAX_TOKENS", str(MAX_TOKENS))),
}

# 벤치 로그
LOG_DIR = Path(os.getenv("RAG_BENCH_LOG_DIR", "./rag_bench_logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("CTX_TOKEN_BUDGET", str(CTX_TOKEN_BUDGET))
os.environ.setdefault("SNIPPET_MAX_CHARS", str(SNIPPET_MAX_CHARS))