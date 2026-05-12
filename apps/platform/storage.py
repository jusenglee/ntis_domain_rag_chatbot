import asyncio
import os
import json
import time
import hashlib
from typing import Optional, Dict, Tuple

class KVStore:
    """memory 저장 백엔드가 따라야 할 최소 async 계약이다."""
    # 운영 로그·메트릭의 dimension(차원 필드)으로 사용한다. 하위 클래스에서 override 한다.
    backend_name: str = "unknown"

    async def get(self, key: str) -> Optional[str]:
        """키에 해당하는 값을 읽거나 없으면 None을 돌려준다."""
        raise NotImplementedError

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """키에 값을 저장하고 필요하면 TTL을 설정한다."""
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        """키에 해당하는 값을 삭제한다."""
        raise NotImplementedError

    async def ping(self) -> bool:
        """백엔드 상태가 사용 가능한지 가볍게 확인한다."""
        return True

    @staticmethod
    def _read_text_file(path: str) -> str:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    @staticmethod
    def _write_text_file(path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    async def close(self) -> None:
        """백엔드 자원을 정리한다."""
        return None


class MemoryKVStore(KVStore):
    """프로세스 메모리에 값과 만료 시각을 함께 보관하는 KVStore 구현체다."""
    backend_name = "memory"

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

    async def delete(self, key: str) -> None:
        """메모리 저장소에서 키를 삭제한다."""
        self._data.pop(key, None)

    async def ping(self) -> bool:
        """백엔드 상태가 사용 가능한지 가볍게 확인한다."""
        return True

    async def close(self) -> None:
        """백엔드 자원을 정리한다."""
        self._data.clear()


class FileKVStore(KVStore):
    """키마다 JSON 파일 하나를 쓰는 간단한 파일 기반 KVStore다."""
    backend_name = "file"

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
            raw = await asyncio.to_thread(self._read_text_file, path)
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
        await asyncio.to_thread(self._write_text_file, path, json.dumps(payload, ensure_ascii=False))

    async def delete(self, key: str) -> None:
        """파일 저장소에서 키에 대응하는 JSON 파일을 삭제한다."""
        path = self._path_for_key(key)
        if os.path.exists(path):
            os.remove(path)

    async def ping(self) -> bool:
        """백엔드 상태가 사용 가능한지 가볍게 확인한다."""
        return True

    async def sweep_expired(self) -> int:
        """루트 디렉토리를 스캔해 만료된 JSON 파일을 일괄 삭제하고 삭제 개수를 돌려준다.

        lazy delete(읽을 때만 만료 정리) 만으로는 접근하지 않는 키가 영구히 남아 디스크가 무한 성장하므로,
        주기 sweeper(청소 작업)에서 호출해 active eviction(능동 만료 제거)을 수행한다.
        """
        def _sweep() -> int:
            removed = 0
            now = time.time()
            try:
                entries = os.listdir(self.root_dir)
            except FileNotFoundError:
                return 0
            except Exception:
                return 0
            for name in entries:
                if not name.endswith(".json"):
                    continue
                path = os.path.join(self.root_dir, name)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        obj = json.loads(handle.read())
                except Exception:
                    continue
                exp = obj.get("expire_at") if isinstance(obj, dict) else None
                if exp is None:
                    continue
                try:
                    if now >= float(exp):
                        try:
                            os.remove(path)
                            removed += 1
                        except FileNotFoundError:
                            pass
                except (TypeError, ValueError):
                    continue
            return removed
        return await asyncio.to_thread(_sweep)
