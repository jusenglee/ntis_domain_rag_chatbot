import sys
import types
import unittest
from unittest import mock


def _install_qdrant_client_stub() -> None:
    if "qdrant_client" in sys.modules:
        return
    qdrant_module = types.ModuleType("qdrant_client")
    http_module = types.ModuleType("qdrant_client.http")
    models_module = types.ModuleType("qdrant_client.http.models")

    class SparseVector:
        def __init__(self, indices, values):
            self.indices = indices
            self.values = values

    class NamedSparseVector:
        def __init__(self, name, vector):
            self.name = name
            self.vector = vector

    models_module.SparseVector = SparseVector
    models_module.NamedSparseVector = NamedSparseVector
    http_module.models = models_module
    qdrant_module.http = http_module
    qdrant_module.QdrantClient = object

    sys.modules["qdrant_client"] = qdrant_module
    sys.modules["qdrant_client.http"] = http_module
    sys.modules["qdrant_client.http.models"] = models_module


_install_qdrant_client_stub()
import retrieval  # noqa: E402


class DummyLegacyClient:
    def __init__(self):
        self.called = False
        self.kwargs = None

    def query_points(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return mock.Mock(points=[])


class DummySearchClient:
    def __init__(self):
        self.called = False
        self.kwargs = None

    def search(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return []


class QdrantSparseCompatTests(unittest.TestCase):
    def test_fallback_to_legacy_query_points_on_named_sparse_error(self) -> None:
        class DummyClient(DummyLegacyClient):
            def __init__(self):
                super().__init__()
                self.first = True

            def query_points(self, **kwargs):
                if self.first:
                    self.first = False
                    raise RuntimeError("NamedSparseVector is not supported")
                return super().query_points(**kwargs)

        client = DummyClient()
        with mock.patch.object(retrieval, "_supports_named_sparse_vector", return_value=True):
            with mock.patch.object(retrieval, "_encode_sparse_query") as encoder:
                encoder.return_value = retrieval.models.SparseVector(indices=[1], values=[0.5])
                result = retrieval._qdrant_sparse_search(
                    client,
                    collection_name="test",
                    query_text="hello",
                    sparse_vector_name="bm25",
                    limit=3,
                    query_filter=None,
                    with_payload=True,
                )

        self.assertEqual(result, [])
        self.assertTrue(client.called)
        self.assertIsInstance(client.kwargs.get("query"), retrieval.models.SparseVector)

    def test_legacy_search_used_when_named_sparse_unsupported(self) -> None:
        client = DummySearchClient()
        with mock.patch.object(retrieval, "_supports_named_sparse_vector", return_value=False):
            with mock.patch.object(retrieval, "_encode_sparse_query") as encoder:
                encoder.return_value = retrieval.models.SparseVector(indices=[1], values=[0.5])
                result = retrieval._qdrant_sparse_search(
                    client,
                    collection_name="test",
                    query_text="hello",
                    sparse_vector_name="bm25",
                    limit=3,
                    query_filter=None,
                    with_payload=True,
                )

        self.assertEqual(result, [])
        self.assertTrue(client.called)
        self.assertIsInstance(client.kwargs.get("query_vector"), retrieval.models.SparseVector)


if __name__ == "__main__":
    unittest.main()
