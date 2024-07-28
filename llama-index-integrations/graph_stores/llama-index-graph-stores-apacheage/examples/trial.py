from llama_index.graph_stores.apacheage import ApacheAGEGraphStore

conf = {
    "database": "postgresDB",
    "user": "postgresUser",
    "password": "postgresPW",
    "host": "localhost",
    "port": 5430,
}

db = ApacheAGEGraphStore(
    graph_name="graph_name",
    conf=conf,
    node_label="Entity"
)

# res = db.get(1688849860263938)
# res = db.get_triplets_from_edge_labels(["Animals"])
# res = db.get_triplets_from_edge_labels_str(["Animals"])
# res = db.get_rel_map(["1688849860263938"])

# query = "MATCH p=()-[:married]-() RETURN p"
# res = db.query(query)

db.upsert_triplet("sub5", "rel", "sub6")

# print(res)