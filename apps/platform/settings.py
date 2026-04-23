# rag_pipeline/settings.py
import os
import logging
from pathlib import Path
from dataclasses import dataclass

import sys
from loguru import logger

# 로깅 설정
class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def setup_logging():
    # Remove default handler
    logger.remove()
    # Add new handler with custom format
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO",
    )
    # Intercept standard logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

setup_logging()


def _get_env_str(name: str, default: str) -> str:
    """환경변수를 문자열로 읽고 공백을 정리한다.
    빈 값은 default로 돌린 뒤 일관된 trim된 문자열로 정책 계산에 넘긴다.
    """
    return str(os.getenv(name, default) or "").strip()


def _get_env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    """정수형 환경변수를 읽고 필요하면 하한선을 검사한다.
    설정 값이 잘못되었을 때 조용히 넘기지 않고 즉시 예외를 내어 부트 시점에 잘못된 env를 드러낸다.
    """
    raw = _get_env_str(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}")
    return value


def _get_env_float(name: str, default: float, *, min_value: float | None = None) -> float:
    """실수형 환경변수를 읽고 필요하면 하한선을 검사한다.
    timeout 같은 설정값이 0 이하로 들어와 runtime이 보정할 수 없는 상태를 여기서 막는다.
    """
    raw = _get_env_str(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}")
    return value


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_local_models_root() -> Path:
    """Resolve the repository-local model asset root."""
    raw = _get_env_str("NTIS_LOCAL_MODELS_ROOT", str(_REPO_ROOT / "Models"))
    return Path(raw).expanduser().resolve()


LOCAL_MODELS_ROOT = str(_resolve_local_models_root())


def _local_models_path(*parts: str) -> str:
    """Build an absolute path under the local model asset root."""
    return str((Path(LOCAL_MODELS_ROOT).joinpath(*parts)).resolve())


def _resolve_local_hf_model_snapshot(model_dir_name: str) -> str:
    """Resolve a Hugging Face cache-style local model directory to a loadable snapshot."""
    model_root = Path(LOCAL_MODELS_ROOT).joinpath(model_dir_name).resolve()
    if not model_root.exists():
        raise ValueError(f"Local model directory not found: {model_root}")

    if (model_root / "config.json").exists():
        return str(model_root)

    ref_main = model_root / "refs" / "main"
    if ref_main.exists():
        snapshot_name = ref_main.read_text(encoding="utf-8").strip()
        if not snapshot_name:
            raise ValueError(f"Local model ref is empty: {ref_main}")
        snapshot_dir = (model_root / "snapshots" / snapshot_name).resolve()
        if (snapshot_dir / "config.json").exists():
            return str(snapshot_dir)
        raise ValueError(f"Local model snapshot missing config.json: {snapshot_dir}")

    raise ValueError(
        f"Local model directory is not directly loadable and has no refs/main snapshot pointer: {model_root}"
    )


def _get_env_or_local_model(env_name: str, default_model_dir_name: str) -> str:
    """Return the explicit env override or resolve the repository-local snapshot default."""
    raw = os.getenv(env_name)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return _resolve_local_hf_model_snapshot(default_model_dir_name)

# Qdrant / Embedding (A)
QDRANT_HOST  = _get_env_str("QDRANT_HOST", "203.250.234.159")
QDRANT_PORT  = _get_env_int("QDRANT_PORT", 8005, min_value=1)
EMBED_MODEL  = _get_env_or_local_model("EMBEDDING_MODEL", "multilingual-e5-large-instruct")

# Qdrant / Embedding (B)
QDRANT_HOST_B  = _get_env_str("QDRANT_HOST_B", QDRANT_HOST)
QDRANT_PORT_B  = _get_env_int("QDRANT_PORT_B", QDRANT_PORT, min_value=1)
EMBED_MODEL_B  = _get_env_or_local_model("EMBEDDING_MODEL_B", "multilingual-e5-large")


def _split_csv(value: str | None, default: list[str]) -> list[str]:
    """CSV 문자열 설정을 공백·빈 항목이 제거된 리스트로 바꾼다.
    allowlist 같은 env가 비어 있을 때 default를 쓰고, 명시적 빈 문자열은 빈 리스트로 해석한다.
    """
    if value is None:
        return list(default)
    value = value.strip()
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


RAG_COLLECTION_ALLOWLIST = _split_csv(
    os.getenv("RAG_COLLECTION_ALLOWLIST"),
    ["ntis_project_v1", "ntis_perf_v1", "ntis_supports_v1"],
)

# Triton
TRITON_URL         = _get_env_str("TRITON_URL", "203.250.234.159:8001")
DEFAULT_MODEL_NAME = _get_env_str("TRITON_MODEL", "gpt_triton_0")
TOKENIZER_MAP = {
    "gemma_triton_0": _local_models_path("gemma-3-27b-it"),
    "gpt_triton_0": _local_models_path("gpt-oss-120b"),
}
SOLAR_TOKENIZER_NAME_OR_PATH = _get_env_str("SOLAR_TOKENIZER_NAME_OR_PATH", _get_env_str("SOLAR_VLLM_MODEL", "/model"))

# 하이퍼파라미터
TOP_K_BASE       = 300
TOP_K_RETURN     = 20
DEFAULT_MAX_TOKENS = _get_env_int("MAX_TOKENS", 16000, min_value=1)
TEMPERATURE      = 0.2
TOP_P            = 0.8
SCORE_THRESHOLD  = 0.20
FUZZ_MIN         = 55
SNIPPET_MAX_CHARS = 8000
MAX_CONTEXT_CHARS = _get_env_int("MAX_CONTEXT_CHARS", 24000, min_value=1)
MAX_DOC_SENTENCES = _get_env_int("MAX_DOC_SENTENCES", 12, min_value=1)
MAX_DOC_TOKENS = _get_env_int("MAX_DOC_TOKENS", 800, min_value=1)
SUMMARY_DOC_SENTENCES = _get_env_int("SUMMARY_DOC_SENTENCES", 6, min_value=1)
SUMMARY_DOC_TOKENS = _get_env_int("SUMMARY_DOC_TOKENS", 240, min_value=1)
RAG_EVIDENCE_TOKEN_BUDGET = _get_env_int("RAG_EVIDENCE_TOKEN_BUDGET", 20000, min_value=1)
RAG_OVERFLOW_QUEUE_SIZE = _get_env_int("RAG_OVERFLOW_QUEUE_SIZE", 4, min_value=0)
RAG_CONTEXT_COMPRESS_MIN_SCORE = _get_env_float("RAG_CONTEXT_COMPRESS_MIN_SCORE", 0.0)
RAG_CONTEXT_COMPRESS_MAX_DOCS = _get_env_int("RAG_CONTEXT_COMPRESS_MAX_DOCS", 4, min_value=0)
RAG_CONTEXT_BODY_MAX_SHARE = _get_env_float("RAG_CONTEXT_BODY_MAX_SHARE", 0.6, min_value=0.0)
RAG_EVIDENCE_EXACT_VERIFY_FLOOR = _get_env_int("RAG_EVIDENCE_EXACT_VERIFY_FLOOR", 512, min_value=1)
RAG_EVIDENCE_EXACT_VERIFY_RATIO = _get_env_float("RAG_EVIDENCE_EXACT_VERIFY_RATIO", 0.1, min_value=0.0)
RAW_PAYLOAD_RECENT_ANCHOR_LIMIT = _get_env_int("RAW_PAYLOAD_RECENT_ANCHOR_LIMIT", 3, min_value=1)
RAW_PAYLOAD_SCHEMA_VERSION = _get_env_str("RAW_PAYLOAD_SCHEMA_VERSION", "v1")
RAW_PAYLOAD_COMPRESSION_CODEC = _get_env_str("RAW_PAYLOAD_COMPRESSION_CODEC", "gzip")

# Redis / 검색 제한 설정
# REDIS_URL: Redis 연결 문자열 (예: redis://localhost:6379)
# REDIS_TTL: Redis 캐시 TTL(초)
# MAX_TOP_K_SIZE: 검색/응답에 사용할 최대 문서 수
REDIS_URL = _get_env_str("REDIS_URL", "redis://redis8:6379")
REDIS_TTL = _get_env_int("REDIS_TTL", 3600, min_value=1)
MAX_TOP_K_SIZE = _get_env_int("MAX_TOP_K_SIZE", 20, min_value=1)

PROMPT_OVERHEAD_TOKENS = _get_env_int("RAG_PROMPT_OVERHEAD_TOKENS", 900, min_value=0)
CTX_SAFETY_MARGIN = _get_env_int("RAG_CTX_SAFETY_MARGIN", 256, min_value=0)
CTX_MIN_BUDGET = _get_env_int("RAG_CTX_MIN_BUDGET", 512, min_value=1)
DEFAULT_MAX_MODEL_LEN = _get_env_int("DEFAULT_MAX_MODEL_LEN", 32768, min_value=1)

def get_ctx_token_budget(model_name: str, *, max_output_tokens: int | None = None) -> int:
    """모델 context 상한에서 prompt overhead와 output budget를 제하고 실제 context 예산을 계산한다.
    LLM에 넘길 문서 분량이 max context를 초과하지 않게 하는 중앙 예산 계산기다.
    """
    model_ctx = MODEL_MAX_CONTEXT.get(model_name, DEFAULT_MAX_MODEL_LEN)

    if max_output_tokens is None:
        max_output_tokens = MAX_TOKENS.get(model_name, DEFAULT_MAX_TOKENS)

    avail = model_ctx - PROMPT_OVERHEAD_TOKENS - int(max_output_tokens) - CTX_SAFETY_MARGIN

    budget = int(avail)
    logger.debug("CTX token budget calculated: %s", budget)
    return max(int(CTX_MIN_BUDGET), budget)

MODEL_MAX_CONTEXT = {
    "solar_vllm_0": _get_env_int("SOLAR_MAX_MODEL_LEN", DEFAULT_MAX_MODEL_LEN, min_value=1),
    "gemma_triton_0": _get_env_int("GEMMA_MAX_MODEL_LEN", DEFAULT_MAX_MODEL_LEN, min_value=1),
    "gpt_triton_0": _get_env_int("GPT_OSS_MAX_MODEL_LEN", DEFAULT_MAX_MODEL_LEN, min_value=1),
}
MAX_TOKENS = {
    "solar_vllm_0": _get_env_int("SOLAR_MAX_TOKENS", DEFAULT_MAX_TOKENS, min_value=1),
    "gemma_triton_0": _get_env_int("GEMMA_MAX_TOKENS", DEFAULT_MAX_TOKENS, min_value=1),
    "gpt_triton_0": _get_env_int("GPT_OSS_MAX_TOKENS", DEFAULT_MAX_TOKENS, min_value=1),
}


def get_model_max_output_tokens(model_name: str) -> int:
    """모델별 최대 생성 토큰 설정을 돌려준다.
    개별 모델 오버라이드가 없으면 전역 default로 돌아가 max token policy가 일관되게 유지된다.
    """
    return int(MAX_TOKENS.get(model_name, DEFAULT_MAX_TOKENS))


def _get_timeout_env(name: str, default: int) -> int:
    """timeout 환경변수를 양의 정수로 읽는 짧은 헬퍼다.
    stream/sync timeout 계산에서 같은 검증 규칙을 재사용하기 위해 분리됐다.
    """
    return _get_env_int(name, default, min_value=1)


def _get_model_timeout_pair(
        *,
        model_env_prefix: str,
        request_type: str,
        default_first: int,
        default_idle: int,
        deprecated_env_prefix: str | None = None,
) -> tuple[int, int]:
    """모델별 request type에 대한 first/idle timeout 쌍을 계산한다.
    신 환경변수 이름을 우선하되 deprecated prefix가 남아 있으면 경고를 남기고 호환해 준다.
    """
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
            first = _get_env_int(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_FIRST", default_first, min_value=1)
        if deprecated_idle is not None:
            logger.warning(
                "[DEPRECATED] %s_%s_TIMEOUT_IDLE is deprecated and will be removed in v0.5.0. "
                "Use %s_TIMEOUT_IDLE instead.",
                deprecated_env_prefix,
                request_type,
                request_env_prefix,
            )
            idle = _get_env_int(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_IDLE", default_idle, min_value=1)

    return first, idle


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
GPT_OSS_STREAM_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GPT_OSS_TRITON_0",
    request_type="STREAM",
    default_first=10,
    default_idle=20,
)
GPT_OSS_SYNC_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GPT_OSS_TRITON_0",
    request_type="SYNC",
    default_first=GPT_OSS_STREAM_TIMEOUTS[0],
    default_idle=GPT_OSS_STREAM_TIMEOUTS[1],
)

TRITON_TIMEOUTS = {
    "gemma_triton_0": {
        "stream": GEMMA_STREAM_TIMEOUTS,
        "sync": GEMMA_SYNC_TIMEOUTS,
    },
    "gpt_triton_0": {
        "stream": GPT_OSS_STREAM_TIMEOUTS,
        "sync": GPT_OSS_SYNC_TIMEOUTS,
    },
}

# 벤치 로그
LOG_DIR = Path(_get_env_str("RAG_BENCH_LOG_DIR", "./rag_bench_logs"))
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


@dataclass(frozen=True)
class SolarVLLMConfig:
    """Solar OpenAI-compat provider에 접속하는 데 필요한 설정 묶음이다.
    model name, base URL, API key, timeout을 하나로 묶어 app/runtime이 해당 provider를 일관된 계약으로 주입하게 한다.
    """
    model_name: str
    base_url: str
    api_key: str
    timeout: float

    @classmethod
    def from_env(cls) -> "SolarVLLMConfig":
        """Solar VLLM 접속 설정을 환경변수에서 읽어 `SolarVLLMConfig`로 만든다.
        필수 필드가 비어 있으면 즉시 예외를 내 부트 오류를 명확히 드러내고, timeout은 실수 형식으로 정규화한다.
        """
        model_name = _get_env_str("SOLAR_VLLM_MODEL", "/model")
        base_url = _get_env_str("SOLAR_VLLM_BASE_URL", "http://203.250.234.159:8010/v1")
        api_key = _get_env_str("SOLAR_VLLM_API_KEY", "EMPTY")

        if not model_name:
            raise ValueError("SOLAR_VLLM_MODEL must not be empty")
        if not base_url:
            raise ValueError("SOLAR_VLLM_BASE_URL must not be empty")

        timeout = _get_env_float("SOLAR_VLLM_TIMEOUT", 120.0, min_value=0.000001)

        return cls(model_name=model_name, base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout)


SOLAR_VLLM_CONFIG = SolarVLLMConfig.from_env()
