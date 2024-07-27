from llama_index.graph_stores.apacheage import ApacheAGEGraphStore

db = ApacheAGEGraphStore(
    user="",
    password="",
    host="",
    port=,
    dbname="",
    graph="",
    node_label=""
)

res = db.get(1688849860263938)
print(res)