import os
import json
import time
import hashlib
from typing import Optional, Dict, Tuple

import aiofiles

class KVStore:
    """memory 저장 백엔드가 따라야 할 최소 async 계약이다."""
    async def get(self, key: str) -> Optional[str]:
        """키에 해당하는 값을 읽거나 없으면 None을 돌려준다."""
        raise NotImplementedError

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """키에 값을 저장하고 필요하면 TTL을 설정한다."""
        raise NotImplementedError

    async def ping(self) -> bool:
        """백엔드 상태가 사용 가능한지 가볍게 확인한다."""
        return True

    async def close(self) -> None:
        """백엔드 자원을 정리한다."""
        return None


class MemoryKVStore(KVStore):
    """프로세스 메모리에 값과 만료 시각을 함께 보관하는 KVStore 구현체다."""
    def __init__(self):
        # key -> (value, expire_at_epoch or None)
        """내부 저장 dict를 초기화한다."""
        self._data: Dict[str, Tuple[str, Optional[float]]] = {}

    async def get(self, key: str) -> Optional[str]:
        """키에 해당하는 값을 읽거나 없으면 None을 돌려준다."""
        item = self._data.get(key)
        if not item:
            return None
        value, exp = item
        if exp is not None and time.time() >= exp:
            self._data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """키에 값을 저장하고 필요하면 TTL을 설정한다."""
        exp = (time.time() + ex) if ex else None
        self._data[key] = (value, exp)

    async def ping(self) -> bool:
        """백엔드 상태가 사용 가능한지 가볍게 확인한다."""
        return True

    async def close(self) -> None:
        """백엔드 자원을 정리한다."""
        self._data.clear()


class FileKVStore(KVStore):
    """키마다 JSON 파일 하나를 쓰는 간단한 파일 기반 KVStore다."""
    def __init__(self, root_dir: str = "local_kvstore"):
        """내부 저장 dict를 초기화한다."""
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)

    def _path_for_key(self, key: str) -> str:
        # 파일명 안전화: key를 해시로 변환
        """원본 key를 SHA-256으로 해시해 파일명으로 바꾼다."""
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.root_dir, f"{h}.json")

    async def get(self, key: str) -> Optional[str]:
        """키에 해당하는 값을 읽거나 없으면 None을 돌려준다."""
        path = self._path_for_key(key)
        if not os.path.exists(path):
            return None
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                raw = await f.read()
            obj = json.loads(raw)
            exp = obj.get("expire_at")
            if exp is not None and time.time() >= float(exp):
                try:
                    os.remove(path)
                except Exception:
                    pass
                return None
            return obj.get("value")
        except Exception:
            return None

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """키에 값을 저장하고 필요하면 TTL을 설정한다."""
        path = self._path_for_key(key)
        expire_at = (time.time() + ex) if ex else None
        payload = {"value": value, "expire_at": expire_at}
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False))

    async def ping(self) -> bool:
        """백엔드 상태가 사용 가능한지 가볍게 확인한다."""
        return True
