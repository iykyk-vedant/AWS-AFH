"""
Export Code Property Graph (GraphRAG) from graph_data.json into a Cypher script for Neo4j.
"""

import json
from pathlib import Path

def generate_cypher():
    json_path = Path("src/api/static/graph_data.json")
    if not json_path.exists():
        print(f"Error: {json_path} does not exist.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    output_dir = Path("data")
    output_dir.mkdir(parents=True, exist_ok=True)
    cypher_file = output_dir / "graph_export.cypher"

    lines = []
    lines.append("// =============================================================================")
    lines.append("// Amaze on Work — Code Property Graph (GraphRAG) Export for iykyk-vedant/AFH-DEMO")
    lines.append(f"// Total Nodes: {len(nodes)} | Total Relationships: {len(edges)}")
    lines.append("// Compatible with Neo4j 4.x / 5.x Desktop, AuraDB, and Neo4j Browser")
    lines.append("// =============================================================================\n")

    # Constraints / Indexes
    lines.append("// --- Constraints & Indexes ---")
    lines.append("CREATE CONSTRAINT file_id_unique IF NOT EXISTS FOR (f:File) REQUIRE f.id IS UNIQUE;")
    lines.append("CREATE CONSTRAINT func_id_unique IF NOT EXISTS FOR (fn:Function) REQUIRE fn.id IS UNIQUE;")
    lines.append("CREATE CONSTRAINT class_id_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.id IS UNIQUE;\n")

    lines.append("// --- Create / Merge Nodes ---")
    group_to_label = {
        "file": "File",
        "function": "Function",
        "class": "Class",
        "module": "Module",
        "incident": "Incident",
        "fix": "Fix"
    }

    for n in nodes:
        nid = n["id"].replace('"', '\\"').replace("'", "\\'")
        name = n.get("label", "").replace('"', '\\"').replace("'", "\\'")
        group = n.get("group", "node").lower()
        label = group_to_label.get(group, "CodeEntity")
        
        props = n.get("properties", {})
        file_path = str(props.get("file", "")).replace("'", "\\'")
        line_num = props.get("line", 0)
        repo = str(props.get("repo", "iykyk-vedant/AFH-DEMO")).replace("'", "\\'")

        cypher = f"MERGE (n:{label} {{id: '{nid}'}}) " \
                 f"SET n.name = '{name}', n.file = '{file_path}', n.line = {line_num}, n.repo = '{repo}';"
        lines.append(cypher)

    lines.append("\n// --- Create / Merge Relationships ---")
    for e in edges:
        from_id = e["from"].replace("'", "\\'")
        to_id = e["to"].replace("'", "\\'")
        rel = e.get("label", "RELATED_TO").upper()
        
        cypher = f"MATCH (a {{id: '{from_id}'}}), (b {{id: '{to_id}'}}) " \
                 f"MERGE (a)-[:{rel}]->(b);"
        lines.append(cypher)

    lines.append("\n// --- Sample Queries for Neo4j Browser ---")
    lines.append("// 1. View Entire Code Property Graph:")
    lines.append("// MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 150;")
    lines.append("// 2. View Functions Called in Shipping Service (INC-003 Root Cause):")
    lines.append("// MATCH (f:File {name: 'shipping_service.py'})-[:CONTAINS]->(fn:Function) OPTIONAL MATCH (fn)-[r:CALLS]->(target) RETURN f, fn, r, target;")
    lines.append("// 3. High Blast-Radius Functions (Most Callers):")
    lines.append("// MATCH (target:Function)<-[:CALLS]-(caller) RETURN target.name, count(caller) as caller_count ORDER BY caller_count DESC LIMIT 10;\n")

    with open(cypher_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Exported {len(nodes)} nodes and {len(edges)} relationships to {cypher_file}")

if __name__ == "__main__":
    generate_cypher()
