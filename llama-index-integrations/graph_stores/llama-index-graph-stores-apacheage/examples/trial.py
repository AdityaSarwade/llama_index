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

res = db.get(1688849860263938)
# res = db.get_rel_map([1688849860263938])
print(res)