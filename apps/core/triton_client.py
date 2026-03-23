# rag_pipeline/triton_client.py
"""\nTriton Inference Server gRPC ?대씪?댁뼵???섑띁 紐⑤뱢.\n\n二쇱슂 湲곕뒫:\n- 紐⑤뜽蹂??좏겕?섏씠? 罹먯떛 諛??꾨＼?꾪듃 ?좏겙 湲몄씠 怨꾩궛\n- max_new_tokens瑜??쒗??湲몄씠??留욊쾶 ?숈쟻?쇰줈 怨꾩궛\n- ?ㅽ듃由щ컢/鍮꾩뒪?몃━諛?怨듭슜 ?뷀듃由??ъ씤??triton_infer()\n\n二쇱쓽 ?ы빆:\n- Triton Python gRPC ?대씪?댁뼵?몃뒗 "?섎굹??InferenceServerClient ?몄뒪?댁뒪??n  ?숈떆 active stream? 1媛?留??덉슜?쒕떎.\n  ????紐⑤뱢?먯꽌??**?ㅽ듃由щ컢?⑹? 留??몄텧留덈떎 蹂꾨룄???대씪?댁뼵???몄뒪?댁뒪**瑜??앹꽦?섍퀬,\n    ?깃????대씪?댁뼵?몃뒗 non-stream(愿由ъ슜) API?먮쭔 ?ъ슜?쒕떎.\n"""

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Dict, List

import numpy as np
from transformers import AutoTokenizer
from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput

from apps.core.settings import (
    TRITON_URL,
    TOKENIZER_MAP,
    TEMPERATURE,
    TOP_P,
    CTX_SAFETY_MARGIN,
    DEFAULT_MAX_MODEL_LEN,
    MODEL_MAX_CONTEXT,
    TRITON_TIMEOUTS,
    get_model_max_output_tokens,
)
from apps.core.settings import logger  # 怨듭슜 logger

# ?깃???Triton ?대씪?댁뼵??(紐⑤뜽 愿由? non-stream ?몄텧??
_triton_client: InferenceServerClient | None = None

# 紐⑤뜽蹂??좏겕?섏씠? 罹먯떆
_tokenizers: Dict[str, AutoTokenizer] = {}

# ?꾨＼?꾪듃 ?좏겙 湲몄씠 罹먯떆 (?댁떆 湲곕컲, LRU)
_PROMPT_TOKEN_CACHE: "OrderedDict[str, int]" = OrderedDict()
_PROMPT_TOKEN_CACHE_LOCK = threading.Lock()
_PROMPT_TOKEN_CACHE_MAX = 1024
_SHORT_PROMPT_CHAR_THRESHOLD = 2000
_SHORT_PROMPT_CHAR_TOKEN_RATIO = 4

# gpt-oss 怨꾩뿴??理쒖쥌 ?듬? ?욎뿉 遺숈씠??留덉빱
ASSISTANT_FINAL_MARKER = "assistantfinal"


# ---------------------------------------------------------------------------
# 0. gpt-oss ?먮퀎 / ?좏겕?섏씠? 愿???좏떥
# ---------------------------------------------------------------------------
def _is_gpt_oss_model(model_name: str) -> bool:
    """紐⑤뜽 ?대쫫??gpt-oss 怨꾩뿴?몄? ?⑥닚 ?⑦꽩?쇰줈 ?먯젙?쒕떎.
    理쒖쥌 ?듬? 留덉빱 泥섎━瑜??곸슜?좎? 寃곗젙?섎뒗 珥덇린 gate濡??대떎.
    """
    name = model_name.lower()
    return ("gpt" in name) and ("oss" in name)


def get_tokenizer_for_model(model_name: str) -> AutoTokenizer:
    """紐⑤뜽蹂?Hugging Face tokenizer瑜?lazy cache濡?媛?몄삩??
    ?꾨＼?꾪듃 ?좏겙 ??怨꾩궛? 鍮덈쾲?섎?濡?媛숈? 紐⑤뜽?????tokenizer 珥덇린??鍮꾩슜?????⑥닔?먯꽌 ?≪닔?쒕떎.
    """
    if model_name not in _tokenizers:
        tok_id = TOKENIZER_MAP[model_name]
        _tokenizers[model_name] = AutoTokenizer.from_pretrained(
            tok_id,
            trust_remote_code=True,
        )
    return _tokenizers[model_name]


def _prompt_cache_key(model_name: str, prompt: str) -> str:
    """紐⑤뜽怨?prompt 議고빀??LRU cache ?ㅻ줈 ?덉젙?곴쾶 ?뺢퇋?뷀븳??
    ?꾨＼?꾪듃 ?꾨Ц??吏곸젒 ?ㅻ줈 ?곗? ?딄퀬 sha256 digest濡?以꾩뿬 硫붾え由??ъ슜?됱쓣 ?듭젣?쒕떎.
    """
    digest = hashlib.sha256()
    digest.update(model_name.encode("utf-8"))
    digest.update(b"|")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


def _get_cached_prompt_tokens(cache_key: str) -> int | None:
    """?꾨＼?꾪듃 ?좏겙 罹먯떆?먯꽌 媛믪쓣 ?쎌쑝硫?LRU ?쒖꽌瑜?媛깆떊?쒕떎.
    ?ㅻ젅??寃쏀빀???쇳븯湲??꾪빐 lock ?덉뿉?쒕쭔 cache瑜?議곗옉?쒕떎.
    """
    with _PROMPT_TOKEN_CACHE_LOCK:
        cached = _PROMPT_TOKEN_CACHE.get(cache_key)
        if cached is None:
            return None
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        return cached


def _set_cached_prompt_tokens(cache_key: str, token_count: int) -> None:
    """怨꾩궛???좏겙 ?섎? prompt cache??湲곕줉?섍퀬 LRU ?ш린瑜??좎??쒕떎.
    ?곹븳???섏쑝硫?媛???ㅻ옒????ぉ??踰꾨젮 ?좏겙 罹먯떆媛 臾댁젣???깆옣?섏? ?딄쾶 ?쒕떎.
    """
    with _PROMPT_TOKEN_CACHE_LOCK:
        _PROMPT_TOKEN_CACHE[cache_key] = token_count
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        if len(_PROMPT_TOKEN_CACHE) > _PROMPT_TOKEN_CACHE_MAX:
            _PROMPT_TOKEN_CACHE.popitem(last=False)


def _get_prompt_tokens(model_name: str, prompt: str) -> int:
    """?꾨＼?꾪듃 湲몄씠瑜??좏겙 ?섎줈 ?섏궛?섍퀬 ?ㅽ뙣 ??fallback 異붿젙移섎? ?ъ슜?쒕떎.
    吏㏃? ?꾨＼?꾪듃??臾몄옄 ??鍮꾨? 異붿젙?쇰줈 泥섎━?섍퀬, 湲??꾨＼?꾪듃???ㅼ젣 tokenizer瑜??ъ슜??max_new_tokens 怨꾩궛 ?뺥솗?꾨? 吏?⑤떎.
    """
    cache_key = _prompt_cache_key(model_name, prompt)
    cached = _get_cached_prompt_tokens(cache_key)
    if cached is not None:
        return cached

    if len(prompt) < _SHORT_PROMPT_CHAR_THRESHOLD:
        estimate = max(1, len(prompt) // _SHORT_PROMPT_CHAR_TOKEN_RATIO)
        _set_cached_prompt_tokens(cache_key, estimate)
        return estimate

    try:
        tok = get_tokenizer_for_model(model_name)
        # special token? ?쒖뒪???꾨＼?꾪듃 ?깆뿉 ?대? ?ы븿?섏뼱 ?덉쓣 ???덉쑝??False
        ids = tok.encode(prompt, add_special_tokens=False)
        token_count = len(ids)
        _set_cached_prompt_tokens(cache_key, token_count)
        return token_count
    except Exception as e:
        logger.warning(f"[TRITON] prompt token 怨꾩궛 ?ㅽ뙣, fallback ?ъ슜: {e}")
        # ?꾩쟾 鍮꾩뿀?쇰㈃ 0 蹂대떎??1 ?댁긽?쇰줈 諛섑솚
        fallback = max(1, len(prompt) // 2)
        _set_cached_prompt_tokens(cache_key, fallback)
        return fallback


# ---------------------------------------------------------------------------
# 1. max_new_tokens ?숈쟻 怨꾩궛
# ---------------------------------------------------------------------------
def _get_max_seq_len(model_name: str) -> int:
    """紐⑤뜽???ㅼ젣濡??뚰솕?????덈뒗 理쒕? context length瑜??삳뒗??
    ?ㅼ젙 ?ㅻ쾭?쇱씠?? tokenizer metadata, default fallback ?쒖꽌濡??덉쟾???곹븳??怨좊Ⅸ??
    """
    max_seq_len = MODEL_MAX_CONTEXT.get(model_name)
    if max_seq_len:
        return int(max_seq_len)

    try:
        tok = get_tokenizer_for_model(model_name)
        max_seq_len = getattr(tok, "model_max_length", DEFAULT_MAX_MODEL_LEN)
        # HF 履쎌뿉??醫낆쥌 ?꾩껌 ??媛?1e30 媛숈?) ?ｌ뼱?먮뒗 寃쎌슦 諛⑹뼱
        if max_seq_len is None or max_seq_len > 100_000:
            return int(DEFAULT_MAX_MODEL_LEN)
        return int(max_seq_len)
    except Exception:
        return int(DEFAULT_MAX_MODEL_LEN)

def _compute_max_new_tokens(
        model_name: str,
        prompt: str,
        max_tokens_hint: int | None = None,
) -> int:
    """?꾨＼?꾪듃 湲몄씠? 紐⑤뜽 context ?곹븳??怨좊젮???앹꽦 ?좏겙 ?곹븳??怨꾩궛?쒕떎.
    ?꾨＼?꾪듃媛 湲곕땲 max context瑜?嫄곗쓽 ???곕뜑?쇰룄 ?꾩쟾??0?쇰줈 留뚮뱾吏 ?딄퀬 理쒖냼 ?앹꽦 遺꾨웾???④꺼 ?쒕퉬???묐떟??蹂댁옣?쒕떎.
    """
    prompt_tokens = _get_prompt_tokens(model_name, prompt)

    max_seq_len = MODEL_MAX_CONTEXT.get(model_name)
    if not max_seq_len:
        try:
            tok = get_tokenizer_for_model(model_name)
            max_seq_len = getattr(tok, "model_max_length", DEFAULT_MAX_MODEL_LEN)
            # HF 履쎌뿉??醫낆쥌 ?꾩껌 ??媛?1e30 媛숈?) ?ｌ뼱?먮뒗 寃쎌슦 諛⑹뼱
            if max_seq_len is None or max_seq_len > 100_000:
                max_seq_len = DEFAULT_MAX_MODEL_LEN
        except Exception:
            max_seq_len = DEFAULT_MAX_MODEL_LEN

    MIN_NEW_TOKENS = 64      # 理쒖냼 ?앹꽦 ?좏겙

    # 紐⑤뜽蹂?湲곕낯 ?곹븳???ъ슜?섍퀬, ?몄옄濡??ㅼ뼱?ㅻ㈃ 洹멸쾬?쇰줈 override
    cap = (
        int(max_tokens_hint)
        if max_tokens_hint is not None
        else int(get_model_max_output_tokens(model_name))
    )

    available = max_seq_len - prompt_tokens - CTX_SAFETY_MARGIN
    if available <= 0:
        logger.warning(
            f"[TRITON] prompt媛 ?대? max_seq_len??嫄곗쓽 ?????곹깭?낅땲?? "
            f"prompt_tokens={prompt_tokens}, max_seq_len={max_seq_len}"
        )
        # 洹몃옒??理쒖냼??議곌툑? ?앹꽦?섎룄濡?        return max(MIN_NEW_TOKENS, min(cap, 128))

    max_new = min(cap, available)
    return max(MIN_NEW_TOKENS, max_new)


# ---------------------------------------------------------------------------
# 2. gpt-oss assistantfinal ?щ㎎ 泥섎━
# ---------------------------------------------------------------------------
def extract_final_answer(raw: str) -> str:
    """gpt-oss ?묐떟 ?띿뒪?몄뿉??`assistantfinal` ?ㅼ쓽 理쒖쥌 ?듬?留?異붿텧?쒕떎.
    留덉빱媛 ?놁쑝硫??먮Ц???좎??섍퀬, ?덉쑝硫??욎そ control text瑜?踰꾨젮 ?ъ슜??媛???듬?留??④릿??
    """
    if not raw:
        return ""

    text = str(raw).strip()
    idx = text.rfind(ASSISTANT_FINAL_MARKER)
    if idx == -1:
        # 留덉빱 ?놁쑝硫?洹몃깷 ?먮낯 諛섑솚
        return text

    final = text[idx + len(ASSISTANT_FINAL_MARKER):]
    # 肄쒕줎/怨듬갚 ?뺣━
    final = final.lstrip(" :\n\t")
    logger.info(final)
    return final.strip()


def stream_after_assistantfinal(chunks):
    """stream chunk ?먮쫫?먯꽌 `assistantfinal` 留덉빱瑜?李얠? ?ㅻ??곕쭔 ?몃?濡??섎젮蹂대궦??
    留덉빱瑜?蹂닿린 ?꾧퉴吏??踰꾪띁留곹븯怨? 留덉빱 ?댄썑??理쒖쥌 ?듬? ?ㅽ듃由쇱쑝濡?媛꾩＜?쒕떎.
    """
    marker = ASSISTANT_FINAL_MARKER.lower()
    seen = False
    buf = ""

    for chunk in chunks:
        if not chunk:
            continue

        buf += chunk

        if not seen:
            pos = buf.lower().find(marker)
            if pos == -1:
                # ?꾩쭅 留덉빱 ???섏솕?쇰㈃ 怨꾩냽 踰꾪띁?먮쭔 ?볦쓬
                continue

            # 泥섏쓬?쇰줈 留덉빱瑜?諛쒓껄???쒖젏
            seen = True
            start = pos + len(ASSISTANT_FINAL_MARKER)
            # 留덉빱 ?욌?遺꾩? 踰꾨━怨? 留덉빱 ?ㅻ????ъ슜
            buf = buf[start:]
            buf = buf.lstrip(" :\n\t")

            if not buf:
                continue

        # ?ш린遺?곕뒗 ?꾨? '理쒖쥌 ?듬?'???대떦
        yield buf
        buf = ""

    # ?ㅽ듃由?醫낅즺 ??留덈Т由?泥섎━
    if seen and buf:
        # assistantfinal ?? ?? ??? ??? ???? ??
        yield buf
    elif not seen and buf:
        # assistantfinal????踰덈룄 ???섏삩 寃쎌슦 fallback:
        # ?꾩껜 踰꾪띁瑜?洹몃깷 蹂대궡嫄곕굹, ?뺤콉???곕씪 踰꾨┫ ?섎룄 ?덉쓬.
        logger.warning(
            "[gpt-oss] assistantfinal 留덉빱瑜?李얠? 紐삵뻽?듬땲?? ?꾩껜 踰꾪띁瑜?洹몃?濡??꾩넚?⑸땲??"
        )
        yield buf


# ---------------------------------------------------------------------------
# 3. Triton ?대씪?댁뼵???앹꽦/?ъ궗??# ---------------------------------------------------------------------------
def get_triton_client() -> InferenceServerClient:
    """non-stream ?몄텧怨?愿由ъ슜 API???ъ궗?⑺븷 singleton Triton client瑜??뚮젮以??
    ?ㅽ듃由щ컢? 蹂꾨룄 client瑜??앹꽦?섎?濡? ???⑥닔??寃곌낵瑜?concurrent stream???곕㈃ ???쒕떎.
    """
    global _triton_client
    if _triton_client is None:
        try:
            _triton_client = InferenceServerClient(url=TRITON_URL, verbose=False)
            logger.info(f"??Triton Client connected to {TRITON_URL}")
        except Exception as e:
            logger.error(f"??Triton Client connection failed: {e}")
            raise e
    return _triton_client


def _make_inputs(
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
):
    """Triton text-generation model??湲곕??섎뒗 numpy input tensor ?명듃瑜?議곕┰?쒕떎.
    ?꾨＼?꾪듃, ?앹꽦 ?곹븳, ?섑뵆留??뚮씪誘명꽣瑜?Triton gRPC ?뺤떇?쇰줈 吏곷젹?뷀븳??
    """
    text = InferInput("text_input", [1], "BYTES")
    text.set_data_from_numpy(
        np.array([prompt.encode("utf-8")], dtype=object)
    )

    # vLLM-backend 沅뚯옣 紐낆묶: sampling_parameters
    sparams = InferInput("sampling_parameters", [1], "BYTES")
    # vLLM Python backend 履?援ы쁽??臾몄옄??湲곕컲 ?뚯떛???ъ슜?섎뒗 寃쎌슦媛 留롮쓬
    params = {
        "temperature": str(float(temperature)),
        "top_p": str(float(top_p)),
        "max_tokens": str(int(max_tokens)),
        # stream ?뚮옒洹몃뒗 蹂꾨룄 BOOL ?명뭼("stream")?쇰줈 ?꾨떖
    }

    sparams.set_data_from_numpy(
        np.array([json.dumps(params).encode("utf-8")], dtype=object)
    )
    return text, sparams


# ---------------------------------------------------------------------------
# 4. ?ㅽ듃由щ컢 ?쒕꼫?덉씠??(Triton gRPC streaming)
# ---------------------------------------------------------------------------
def _create_stream_client() -> InferenceServerClient:
    """?ㅽ듃由щ컢 ?몄텧???쇳쉶??Triton client瑜??앹꽦?쒕떎.
    ??client??active stream ?섎굹留??덉슜?섎뒗 Triton ?쒖빟 ?뚮Ц???붿껌留덈떎 ???몄뒪?댁뒪瑜?留뚮뱺??
    """
    return InferenceServerClient(url=TRITON_URL, verbose=False)


def _resolve_stream_timeouts(
        model_name: str,
        request_type: str,
        first_token_timeout: int | None,
        idle_timeout: int | None,
) -> tuple[int, int]:
    """?ㅽ듃由щ컢 ?몄뀡???곸슜??TTFT쨌?앹꽦쨌?꾩껜 ?곕뱶?쇱씤???ㅼ젙?먯꽌 ?댁꽍?쒕떎.
    ?몄텧?먭? ?ъ젙?섑븳 ??꾩븘?껋씠 ?덉쑝硫?洹?媛믪쓣 ?곗꽑?섍퀬, ?놁쑝硫?紐⑤뜽蹂?default濡??뚯븘媛꾨떎.
    """
    fallback_first = 10 if first_token_timeout is None else int(first_token_timeout)
    fallback_idle = 20 if idle_timeout is None else int(idle_timeout)
    request_timeouts = TRITON_TIMEOUTS.get(model_name, {})
    model_timeouts = request_timeouts.get(request_type, None)
    if model_timeouts:
        model_first, model_idle = model_timeouts
        return (
            model_first if first_token_timeout is None else int(first_token_timeout),
            model_idle if idle_timeout is None else int(idle_timeout),
        )
    return fallback_first, fallback_idle


def _triton_stream_generator(
        model_name: str,
        prompt: str,
        text: InferInput,
        sparams: InferInput,
        first_token_timeout: int | None = 10,
        idle_timeout: int | None = 20,
        request_type: str = "stream",
):
    """Triton ?ㅽ듃由щ컢 callback??Python generator濡?諛붽퓞 ?곸쐞 ?덉씠?닿? ?쒖감 ?뚮퉬?섍쾶 ?쒕떎.
    肄쒕갚?먯꽌 ?ㅼ뼱??chunk, ?ㅻ쪟, timeout ?곹깭瑜??섎굹???먮쫫?쇰줈 蹂?섑빐 SSE ?몄텧怨?濡쒓렇 怨꾩륫??媛숈? 怨꾩빟???곌쾶 ?쒕떎.
    """
    first_token_timeout, idle_timeout = _resolve_stream_timeouts(
        model_name,
        request_type,
        first_token_timeout,
        idle_timeout,
    )
    cli = _create_stream_client()  # ?좑툘 ?ㅽ듃由щ컢?⑹쑝濡?蹂꾨룄 ?대씪?댁뼵???앹꽦

    stream_flag = InferInput("stream", [1], "BOOL")
    stream_flag.set_data_from_numpy(np.array([True], dtype=bool))

    outs = [InferRequestedOutput("text_output")]

    q: List[str] = []
    done = threading.Event()

    def on_resp(result, error):
        """Triton stream callback?먯꽌 ?묐떟 chunk???덉쇅瑜?queue???곸옱?쒕떎.
        ?앹꽦???ㅻ젅?쒖? callback ?ㅻ젅???ъ씠??寃쎄퀎瑜???queue媛 留욎텛硫? 醫낅즺 sentinel??媛숈씠 ?ｋ뒗??
        """
        if error:
            logger.error(f"[ERR] Triton Callback Error: {error}")
            done.set()
            return

        if result is None:
            done.set()
            return

        arr = result.as_numpy("text_output")
        if arr is not None and len(arr) > 0:
            raw = arr[0]
            chunk = raw.decode("utf-8", errors="ignore")
            q.append(chunk)

        is_final = False
        try:
            resp = result.get_response()
            params = getattr(resp, "parameters", None)
            if params:
                flag = params.get("triton_final_response")
                if flag and getattr(flag, "bool_param", False):
                    is_final = True
        except Exception as e:
            logger.debug(f"[STREAM] triton_final_response check failed: {e}")

        if is_final:
            done.set()
            return

    # ?ㅽ듃由??쒖옉
    cli.start_stream(callback=on_resp)
    cli.async_stream_infer(
        model_name,
        inputs=[text, sparams, stream_flag],
        outputs=outs,
    )

    try:
        start_time = time.time()
        last_yield_time = start_time
        got_first = False

        while not done.is_set() or q:
            if q:
                chunk = q.pop(0)
                got_first = True
                last_yield_time = time.time()
                yield chunk
            else:
                now = time.time()
                if not got_first and (now - start_time > first_token_timeout):
                    logger.warning("[WARN] First token timeout")
                    break
                if got_first and (now - last_yield_time > idle_timeout):
                    logger.warning("[WARN] Idle timeout after response started")
                    break
                time.sleep(0.005)
    finally:
        # ?ㅽ듃由?醫낅즺 (?먮윭/?뺤긽 ?щ?? ?곴??놁씠)
        try:
            cli.stop_stream()
        except Exception as e:
            logger.warning(f"[STREAM] stop_stream failed: {e}")


# ---------------------------------------------------------------------------
# 5. ?숆린(internal) infer (?ㅽ듃由щ컢 ??accumulate)
# ---------------------------------------------------------------------------
def _triton_infer_sync(
        model_name: str,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
) -> str:
    # 1) Triton ?낅젰 ?먯꽌 援ъ꽦
    """non-stream Triton inference瑜??ㅽ뻾?섍퀬 ?띿뒪???묐떟??諛섑솚?쒕떎.
    異쒕젰 tensor ?뺤떇??bytes?대뱺 string?대뱺 媛숈? 臾몄옄?대줈 蹂듭썝???곸쐞 ?뚯씠?꾨씪?몄씠 紐⑤뜽 醫낅쪟 李⑥씠瑜??섏떇?섏? ?딄쾶 ?쒕떎.
    """
    text, sparams = _make_inputs(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

    # 2) ?ㅽ듃由щ컢 ???꾩껜 臾몄옄??accumulate
    accumulated_text = ""
    for chunk in _triton_stream_generator(
            model_name,
            prompt,
            text,
            sparams,
            first_token_timeout=10,
            idle_timeout=20,
            request_type="sync",
    ):
        accumulated_text += chunk

    accumulated_text = accumulated_text.strip()

    # 3) gpt-oss 怨꾩뿴?대㈃ assistantfinal ?댄썑留?異붿텧
    if _is_gpt_oss_model(model_name):
        return extract_final_answer(accumulated_text)

    return accumulated_text


# ---------------------------------------------------------------------------
# 6. 怨듭슜 ?뷀듃由??ъ씤?? triton_infer()
# ---------------------------------------------------------------------------
def log_prompt_tokens(model_name: str, prompt: str, tag: str = ""):
    """?꾩옱 prompt??????좏겙 異붿젙移섏? ?앹꽦 ?곹븳??濡쒓렇濡??④릿??
    紐⑤뜽??context limit??媛源앹＜ ?뚯븘?쒕뒗吏 triage????蹂대뒗 愿痢??ъ씤?몃떎.
    """
    try:
        tok = get_tokenizer_for_model(model_name)
        ids = tok.encode(prompt, add_special_tokens=False)
        logger.info(
            f"[TRITON][TOK] {tag} model={model_name}, "
            f"prompt_chars={len(prompt)}, prompt_tokens={len(ids)}"
        )
    except Exception as e:
        logger.warning(f"[TRITON][TOK] token count failed: {e}")

def triton_infer(
        model_name: str,
        prompt: str,
        *,
        stream: bool = True,
        max_tokens: int | None = None,
        temperature: float = TEMPERATURE,
        top_p: float = TOP_P,
        timeout_first: int | None = None,
        timeout_idle: int | None = None,
):
    """?ㅽ듃由щ컢怨?鍮꾩뒪?몃━諛?Triton ?몄텧???듯빀?섎뒗 理쒖긽???뷀듃由ы룷?명듃??
    紐⑤뜽蹂?max_new_tokens 怨꾩궛, gpt-oss final answer ?뺣━, timeout 怨꾩륫, stream/filter ?꾩쿂由щ? ??怨녹뿉??留욎텣??
    """
    logger.info(f"[TRITON] infer start - model={model_name}, len={len(prompt)}")
    log_prompt_tokens(model_name, prompt, tag="infer")

    if max_tokens is None:
        max_tokens = int(get_model_max_output_tokens(model_name))

    dynamic_max_tokens = _compute_max_new_tokens(
        model_name=model_name,
        prompt=prompt,
        max_tokens_hint=max_tokens,
    )

    if stream:
        # ?ㅽ듃由щ컢 紐⑤뱶
        timeout_first, timeout_idle = _resolve_stream_timeouts(
            model_name,
            "stream",
            timeout_first,
            timeout_idle,
        )
        text, sparams = _make_inputs(
            prompt,
            max_tokens=dynamic_max_tokens,
            temperature=float(TEMPERATURE if temperature is None else temperature),
            top_p=float(TOP_P if top_p is None else top_p),
            top_k=top_k,
        )

        base_gen = _triton_stream_generator(
            model_name,
            prompt,
            text,
            sparams,
            first_token_timeout=timeout_first,
            idle_timeout=timeout_idle,
            request_type="stream",
        )

        # gpt-oss 怨꾩뿴?대㈃ assistantfinal ?댄썑留??ㅽ듃由щ컢
        if _is_gpt_oss_model(model_name):
            logger.info("[TRITON] gpt-oss 紐⑤뜽 媛먯? ??assistantfinal ?댄썑留??ㅽ듃由щ컢")
            return stream_after_assistantfinal(base_gen)

        # 洹???紐⑤뜽? raw ?ㅽ듃由?洹몃?濡?        return base_gen

    # Sync Path: ?ㅽ듃由щ컢???대??곸쑝濡??ъ슜?섏뿬 理쒖쥌 臾몄옄??諛섑솚
    return _triton_infer_sync(
        model_name,
        prompt,
        max_tokens=dynamic_max_tokens,
        temperature=float(TEMPERATURE if temperature is None else temperature),
        top_p=float(TOP_P if top_p is None else top_p),
    )


