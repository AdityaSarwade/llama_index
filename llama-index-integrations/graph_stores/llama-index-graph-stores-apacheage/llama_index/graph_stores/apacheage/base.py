"""Apache AGE graph store index."""

import re
import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Tuple, Union, Optional

from llama_index.core.graph_stores.types import GraphStore

# if TYPE_CHECKING:
import psycopg2.extras

logger = logging.getLogger(__name__)


class AGEQueryException(Exception):
    """Exception for the AGE queries."""

    def __init__(self, exception: Union[str, Dict]) -> None:
        if isinstance(exception, dict):
            self.message = exception["message"] if "message" in exception else "unknown"
            self.details = exception["details"] if "details" in exception else "unknown"
        else:
            self.message = exception
            self.details = "unknown"

    def get_message(self) -> str:
        return self.message

    def get_details(self) -> Any:
        return self.details


class ApacheAGEGraphStore(GraphStore):
    """Apache AGE Graph Store"""

    # python type mapping for providing readable types to LLM
    types = {
        "str": "STRING",
        "float": "DOUBLE",
        "int": "INTEGER",
        "list": "LIST",
        "dict": "MAP",
        "bool": "BOOLEAN",
    }

    # precompiled regex for checking chars in graph labels
    label_regex = re.compile("[^0-9a-zA-Z]+")

    def __init__(
        self,
        graph_name: str,
        conf: Dict[str, Any],
        node_label: str = "Entity",
        create: bool = True,
        **kwargs,
    ) -> None:
        """
        Create a new AGEGraph instance.

        Args:
        graph_name (str): the name of the graph to connect to or create
        conf (Dict[str, Any]): the pgsql connection config passed directly
            to psycopg2.connect
        create (bool): if True and graph doesn't exist, attempt to create it

        ### conf example:
        ```
        conf = {
            "database": "postgresDB",
            "user": "postgresUser",
            "password": "postgresPW",
            "host": "localhost",
            "port": 5432,
        }
        graph = AGEGraph(graph_name="age_test", conf=conf)
        ```

        *Security note* : Make sure that the database connection uses credentials
        that are narrowly-scoped to only include necessary permissions.
        Failure to do so may result in data corruption or loss, since the calling
        code may attempt commands that would result in deletion, mutation
        of data if appropriately prompted or reading sensitive data if such
        data is present in the database.
        The best way to guard against such negative outcomes is to (as appropriate)
        limit the permissions granted to the credentials used with this tool.
        """
        self._driver = psycopg2.connect(**conf)

        self.graph_name = graph_name
        self.node_label = node_label
        self.schema = ""

        with self._get_cursor() as curs:
            # check if graph with name graph_name exists
            graph_id_query = (
                """SELECT graphid FROM ag_catalog.ag_graph WHERE name = '{}'""".format(
                    graph_name
                )
            )

            curs.execute(graph_id_query)
            data = curs.fetchone()

            # if graph doesn't exist and create is True, create it
            if data is None:
                if create:
                    create_statement = """
                        SELECT ag_catalog.create_graph('{}');
                    """.format(
                        graph_name
                    )

                    try:
                        curs.execute(create_statement)
                        self._driver.commit()
                    except psycopg2.Error as e:
                        raise AGEQueryException(
                            {
                                "message": "Could not create the graph",
                                "detail": str(e),
                            }
                        )

                else:
                    raise Exception(
                        (
                            'Graph "{}" does not exist in the database '
                            + 'and "create" is set to False'
                        ).format(graph_name)
                    )

                curs.execute(graph_id_query)
                data = curs.fetchone()

            # store graph id and refresh the schema
            self.graphid = data.graphid
            # self.refresh_schema()

    def _get_cursor(self) -> psycopg2.extras.NamedTupleCursor:
        """
        get cursor, load age extension and set search path
        """

        try:
            import psycopg2.extras
        except ImportError as e:
            raise ImportError(
                "Unable to import psycopg2, please install with "
                "`pip install -U psycopg2`."
            ) from e
        cursor = self._driver.cursor(cursor_factory=psycopg2.extras.NamedTupleCursor)
        cursor.execute("""LOAD 'age';""")
        cursor.execute("""SET search_path = ag_catalog, "$user", public;""")
        return cursor

    @property
    def client(self) -> None:
        return self._driver

    def get(self, subj: str) -> List[List[str]]:
        """Get triplets."""
        query = """
            SELECT * FROM ag_catalog.cypher('{graph_name}', $$
            MATCH (a:{node_label})-[e]->(b:{node_label})
            WHERE id(a) = {subj}
            RETURN type(e), id(b)
        $$) AS (edge agtype, b_id agtype);
        """
        triplets = []

        with self._get_cursor() as curs:
            q = query.format(
                graph_name=self.graph_name,
                node_label=self.node_label,
                subj=subj,
            )
            try:
                curs.execute(q)
                data = curs.fetchall()
                for d in data:
                    triplets.append([d.edge, d.b_id])
            except psycopg2.Error as e:
                raise AGEQueryException(
                    {
                        "message": "Error fetching triplets",
                        "detail": str(e),
                    }
                )

        return triplets

    def get_labels(self) -> Tuple[List[str], List[str]]:
        """
        Get all labels of a graph (for both edges and vertices)
        by querying the graph metadata table directly

        Returns
            Tuple[List[str]]: 2 lists, the first containing vertex
                labels and the second containing edge labels
        """

        e_labels_records = self.query(
            """MATCH ()-[e]-() RETURN collect(distinct label(e)) as labels"""
        )
        e_labels = e_labels_records[0]["labels"] if e_labels_records else []

        n_labels_records = self.query(
            """MATCH (n) RETURN collect(distinct label(n)) as labels"""
        )
        n_labels = n_labels_records[0]["labels"] if n_labels_records else []

        return n_labels, e_labels

    def get_triplets_from_edge_labels(
        self, edge_labels: List[str]
    ) -> List[Dict[str, str]]:
        """
        Get a set of distinct relationship types (as a list of dicts) in the graph
        from the edge labels

        Args:
            edge_labels (List[str]): a list of edge labels to filter for

        Returns:
            List[Dict[str, str]]: relationships as a list of dicts in the format
                "{'start':<from_label>, 'type':<edge_label>, 'end':<from_label>}"
        """

        # age query to get distinct relationship types
        try:
            import psycopg2
        except ImportError as e:
            raise ImportError(
                "Unable to import psycopg2, please install with "
                "`pip install -U psycopg2`."
            ) from e
        query = """
        SELECT * FROM ag_catalog.cypher('{graph_name}', $$
            MATCH (a)-[e:`{edge_label}`]->(b)
            WITH a,e,b LIMIT 3000
            RETURN DISTINCT labels(a) AS from, type(e) AS edge, labels(b) AS to
            LIMIT 10
        $$) AS (f agtype, edge agtype, t agtype);
        """

        triplets = []

        # iterate desired edge types and add distinct relationship types to result
        with self._get_cursor() as curs:
            for label in edge_labels:
                q = query.format(graph_name=self.graph_name, edge_label=label)
                try:
                    curs.execute(q)
                    data = curs.fetchall()
                    for d in data:
                        # use json.loads to convert returned
                        # strings to python primitives
                        triplets.append(
                            {
                                "start": json.loads(d.f)[0],
                                "type": json.loads(d.edge),
                                "end": json.loads(d.t)[0],
                            }
                        )
                except psycopg2.Error as e:
                    raise AGEQueryException(
                        {
                            "message": "Error fetching triplets",
                            "detail": str(e),
                        }
                    )

        return triplets
