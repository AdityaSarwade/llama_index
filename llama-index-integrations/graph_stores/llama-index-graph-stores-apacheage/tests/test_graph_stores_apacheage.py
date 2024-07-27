from unittest.mock import MagicMock, patch

from llama_index.core.graph_stores.types import GraphStore
from llama_index.graph_stores.apacheage import ApacheAGEGraphStore


@patch("llama_index.graph_stores.apacheage.ApacheAGEGraphStore")
def test_neo4j_graph_store(MockApacheAGEGraphStore: MagicMock):
    instance: ApacheAGEGraphStore = MockApacheAGEGraphStore.return_value()
    assert isinstance(instance, GraphStore)
