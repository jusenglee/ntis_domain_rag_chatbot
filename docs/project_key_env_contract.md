# Project Key Env 계약

이 문서는 `pjt_id`와 `pjt_no` 처리에 대한 environment contract를 기록합니다.

## 기준선

- `RAG_KEY_PJT_ID=pjt_id`
- `RAG_KEY_PJT_NO=pjt_no`

이 두 키는 같은 payload field로 해석되면 안 됩니다.

## Startup Validation

Startup validation은 app runtime initialization 중에 실행됩니다.
현재 runtime 경로:
- thin entry: `apps/api/main.py`
- app assembly: `apps/api/app_factory.py`
- startup validation: `apps/api/runtime.py`

`RAG_KEY_PJT_ID`와 `RAG_KEY_PJT_NO`가 같은 의미로 collapse되면 startup은 fail-fast 해야 합니다.

## Join / Lookup 규칙

- `pjt_id`는 instance key입니다.
- `pjt_no`는 group key입니다.
- lookup/join code는 하나의 seed map 안에서 둘을 섞으면 안 됩니다.
- group join resolution이 `pjt_no -> pjt_id`로 확장될 수는 있지만, 그것이 원래 semantic distinction을 바꾸지는 않습니다.

