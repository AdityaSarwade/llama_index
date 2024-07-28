"""Apache AGE graph store index."""

import re
import json
import logging
from json import JSONDecodeError
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

    def get_schema(self, refresh: bool = False) -> str:
        """Get the schema of the Apache AGE Graph store."""
        if self.schema and not refresh:
            return self.schema
        # self.refresh_schema()
        logger.debug(f"get_schema() schema:\n{self.schema}")
        return self.schema

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

    def get_rel_map(
        self, subjs: Optional[List[str]] = None, depth: int = 2, limit: int = 30
    ) -> Dict[str, List[List[str]]]:
        """Get depth-aware rel map."""
        formatted_subjs = [subj.lower() for subj in subjs] if subjs else []
        query = """
            SELECT * FROM ag_catalog.cypher('{graph_name}', $$
            MATCH p=(n1:{node_label})-[*1..{depth}]->()
            WHERE n1.id IN {formatted_subjs}
            UNWIND relationships(p) AS rel
            WITH n1.id AS subj, collect(DISTINCT (type(rel) + ':' + endNode(rel).id)) AS flattened_rels
            RETURN subj, flattened_rels
            LIMIT {limit}
        $$) AS (subj agtype, flattened_rels agtype);
        """

        rel_map: Dict[Any, List[Any]] = {}
        if subjs is None or len(subjs) == 0:
            # unlike simple graph_store, we don't do get_all here
            return rel_map

        with self._get_cursor() as curs:
            q = query.format(
                graph_name=self.graph_name,
                node_label=self.node_label,
                depth=depth,
                formatted_subjs=formatted_subjs,
                limit=limit,
            )
            try:
                curs.execute(q)
                data = curs.fetchall()
                if not data:
                    return rel_map
                for d in data:
                    rel_map[d["subj"]] = d["flattened_rels"]
            except psycopg2.Error as e:
                raise AGEQueryException(
                    {
                        "message": "Error fetching triplets",
                        "detail": str(e),
                    }
                )

    def upsert_triplet(self, subj: str, rel: str, obj: str) -> None:
        """Add triplet."""
        query = """
            SELECT * FROM ag_catalog.cypher('{graph_name}', $$
            MERGE (n1:{node_label} {{id:'{subj}'}})
            MERGE (n2:{node_label} {{id:'{obj}'}})
            MERGE (n1)-[:{rel}]->(n2)
            $$) as (n1 agtype);
        """

        with self._get_cursor() as curs:
            q = query.format(
                graph_name=self.graph_name.strip(),
                node_label=self.node_label.strip(),
                subj=subj,
                obj=obj,
                rel=rel.replace(" ", "_").upper(),
            )
            try:
                curs.execute(q)
                self._driver.commit()
            except psycopg2.Error as e:
                self._driver.rollback()
                raise AGEQueryException(
                    {
                        "message": "Error adding triplet",
                        "detail": str(e),
                    }
                )

    def query(self, query: str, param_map: Optional[Dict[str, Any]] = {}) -> Any:
        """
        Query the graph by taking a cypher query, converting it to an
        age compatible query, executing it and converting the result

        Args:
            query (str): a cypher query to be executed
            params (dict): parameters for the query (not used in this implementation)

        Returns:
            List[Dict[str, Any]]: a list of dictionaries containing the result set
        """
        try:
            import psycopg2
        except ImportError as e:
            raise ImportError(
                "Unable to import psycopg2, please install with "
                "`pip install -U psycopg2`."
            ) from e

        # convert cypher query to pgsql/age query
        wrapped_query = self._wrap_query(query, self.graph_name)

        # execute the query, rolling back on an error
        with self._get_cursor() as curs:
            try:
                curs.execute(wrapped_query)
                self._driver.commit()
            except psycopg2.Error as e:
                self._driver.rollback()
                raise AGEQueryException(
                    {
                        "message": "Error executing graph query: {}".format(query),
                        "detail": str(e),
                    }
                )

            data = curs.fetchall()
            if data is None:
                result = []
            # convert to dictionaries
            else:
                try:
                    result = [self._record_to_dict(d) for d in data]
                except JSONDecodeError as e:
                    logger.error(e)
                    logger.error(
                        "Failed to convert to dict, returning NamedTuple. Try to return only simple data types."
                    )
                    result = [d for d in data]
                except Exception as e:
                    logger.error(e)
                    logger.error("Failed to convert to dict after query.")
                    raise AGEQueryException(
                        {
                            "message": "Failed to convert to dict after query.",
                            "detail": str(e),
                        }
                    )

            return result

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

    def get_triplets_from_edge_labels_str(self, edge_labels: List[str]) -> List[str]:
        """
        Get a set of distinct relationship types (as a list of strings) in the graph
        from the edge labels

        Args:
            edge_labels (List[str]): a list of edge labels to filter for

        Returns:
            List[str]: relationships as a list of strings in the format
                "(:`<from_label>`)-[:`<edge_label>`]->(:`<to_label>`)"
        """

        triplets = self.get_triplets_from_edge_labels(edge_labels)

        return self._format_triplets(triplets)

    @staticmethod
    def _format_triplets(triplets: List[Dict[str, str]]) -> List[str]:
        """
        Convert a list of relationships from dictionaries to formatted strings

        Args:
            triplets (List[Dict[str,str]]): a list relationships in the form
                {'start':<from_label>, 'type':<edge_label>, 'end':<from_label>}

        Returns:
            List[str]: a list of relationships in the form
                "(:`<from_label>`)-[:`<edge_label>`]->(:`<to_label>`)"
        """
        triplet_template = "(:`{start}`)-[:`{type}`]->(:`{end}`)"
        triplet_schema = [triplet_template.format(**triplet) for triplet in triplets]

        return triplet_schema

    @staticmethod
    def _record_to_dict(record: NamedTuple) -> Dict[str, Any]:
        """
        Convert a record returned from an age query to a dictionary

        Args:
            record (): a record from an age query result

        Returns:
            Dict[str, Any]: a dictionary representation of the record where
                the dictionary key is the field name and the value is the
                value converted to a python type
        """
        # result holder
        d = {}

        # prebuild a mapping of vertex_id to vertex mappings to be used
        # later to build edges
        vertices = {}
        for k in record._fields:
            v = getattr(record, k)
            # agtype comes back '{key: value}::type' which must be parsed
            if isinstance(v, str) and "::" in v:
                dtype = v.split("::")[-1]
                v = v.split("::")[0]
                if dtype == "vertex":
                    vertex = json.loads(v)
                    vertices[vertex["id"]] = vertex.get("properties")

        # iterate returned fields and parse appropriately
        for k in record._fields:
            v = getattr(record, k)
            if isinstance(v, str) and "::" in v:
                dtype = v.split("::")[-1]
                v = v.split("::")[0]
            else:
                dtype = ""

            if dtype == "vertex":
                d[k] = json.loads(v).get("properties")
            # convert edge from id-label->id by replacing id with node information
            # we only do this if the vertex was also returned in the query
            # this is an attempt to be consistent with neo4j implementation
            elif dtype == "edge":
                edge = json.loads(v)
                d[k] = (
                    vertices.get(edge["start_id"], {}),
                    edge["label"],
                    vertices.get(edge["end_id"], {}),
                )
            else:
                d[k] = json.loads(v) if isinstance(v, str) else v

        return d

    @staticmethod
    def _get_col_name(field: str, idx: int) -> str:
        """
        Convert a cypher return field to a pgsql select field
        If possible keep the cypher column name, but create a generic name if necessary

        Args:
            field (str): a return field from a cypher query to be formatted for pgsql
            idx (int): the position of the field in the return statement

        Returns:
            str: the field to be used in the pgsql select statement
        """
        # remove white space
        field = field.strip()
        # if an alias is provided for the field, use it
        if " as " in field:
            return field.split(" as ")[-1].strip()
        # if the return value is an unnamed primitive, give it a generic name
        elif field.isnumeric() or field in ("true", "false", "null"):
            return f"column_{idx}"
        # otherwise return the value stripping out some common special chars
        else:
            return field.replace("(", "_").replace(")", "")

    @staticmethod
    def _wrap_query(query: str, graph_name: str) -> str:
        """
        Convert a cypher query to an Apache Age compatible
        sql query by wrapping the cypher query in ag_catalog.cypher,
        casting results to agtype and building a select statement

        Args:
            query (str): a valid cypher query
            graph_name (str): the name of the graph to query

        Returns:
            str: an equivalent pgsql query
        """

        # pgsql template
        template = """SELECT {projection} FROM ag_catalog.cypher('{graph_name}', $$
            {query}
        $$) AS ({fields});"""

        # if there are any returned fields they must be added to the pgsql query
        if "return" in query.lower():
            # parse return statement to identify returned fields
            fields = (
                query.lower()
                .split("return")[-1]
                .split("distinct")[-1]
                .split("order by")[0]
                .split("skip")[0]
                .split("limit")[0]
                .split(",")
            )

            # raise exception if RETURN * is found as we can't resolve the fields
            if "*" in [x.strip() for x in fields]:
                raise ValueError(
                    "AGE graph does not support 'RETURN *'"
                    + " statements in Cypher queries"
                )

            # get pgsql formatted field names
            fields = [
                ApacheAGEGraphStore._get_col_name(field, idx)
                for idx, field in enumerate(fields)
            ]

            # build resulting pgsql relation
            fields_str = ", ".join(
                [field.split(".")[-1] + " agtype" for field in fields]
            )

        # if no return statement we still need to return a single field of type agtype
        else:
            fields_str = "a agtype"

        select_str = "*"

        return template.format(
            graph_name=graph_name,
            query=query,
            fields=fields_str,
            projection=select_str,
        )
