"""Apache AGE graph store index."""

import logging
from typing import Any, Dict, List, Optional

from llama_index.core.graph_stores.types import GraphStore

import age

logger = logging.getLogger(__name__)


class ApacheAGEGraphStore(GraphStore):
    """Apache AGE Graph Store"""

    def __init__(
        self,
        user: str,
        password: str,
        host: str = "localhost",
        port: int = 5432,
        dbname="postgresDB",
        graph: str = "age_graph",
        node_label: str = "Entity",
        **kwargs,
    ) -> None:
        """Initialize Params."""

        self._driver = age.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            dbname=dbname,
            # load_from_plugins=True,
            graph=graph,
        )
        self._node_label = node_label
        self._dbname = dbname
        self._graph = graph

        self.schema = ""

    @property
    def client(self) -> None:
        return self._driver

    def get(self, subj: str) -> List[List[str]]:
        """Get triplets."""
        query = f"""
            MATCH (n1:{self._node_label})-[r]->(n2:{self._node_label})
            WHERE id(n1) = %s
            RETURN id(n1), label(r), id(n2)
        """

        cursor = self._driver.execCypher(
            query, cols=["n1 VARCHAR, r VARCHAR", "n2 VARCHAR"], params=(subj,)
        )
        triplets = []
        for row in cursor:
            triplets.append([row[0], row[1], row[2]])
        return triplets
