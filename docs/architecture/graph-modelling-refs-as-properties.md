# Graph modelling: external references as node properties, not stub nodes

**Status:** Adopted
**Applies to:** any property graph (Neo4j, Memgraph, JanusGraph, etc.) built by enriching a corpus against an external knowledge source (Wikidata, OpenCorporates, DBpedia, ASIC, internal MDM systems, …).
**Owner:** data-platform / knowledge-graph engineering

## TL;DR

> If an entity from the external source is **not itself part of your corpus**, do **not** model it as a node. Carry its identifier + label as **properties on the source node** (`{rel}_qids`, `{rel}_labels`). Edges in the graph exist only between two corpus nodes.

## Context — the problem

When you enrich a corpus entity from a public knowledge graph (e.g. fetch the Wikidata page for *Telstra*), the enrichment response is densely linked: `instance_of → business`, `country → Australia`, `headquarters → Melbourne`, `industry → telecommunications`, `developed_by → some external org`. Many of these link targets are **not** themselves entities in your corpus — they're shared reference data the public KG happens to use as the back-end of its type system or its set of common values.

The naïve approach is to take every such linked target QID, mint a node for it, and create the edge. This produces three concrete pathologies:

1. **Stub explosion.** A 15k-entity corpus accreted ~3,800 placeholder nodes representing Wikidata QIDs that nobody in the corpus actually *is*. These nodes have no description, no provenance, no business meaning — they exist only so the edge has somewhere to land.
2. **Misleading "gravity" / centrality.** Aggregate metrics get dominated by stubs. `Q5 (human)` becomes a 3,993-degree hub even though no human in your corpus *is* it — they're all `:Person`-class. Top-hub queries return type-system anchors instead of the entities you actually care about.
3. **Conceptual confusion.** Type membership is now expressed *twice*: once as a Neo4j label (`:Person`) and once as an outgoing edge (`-[:INSTANCE_OF]->(Q5)`). Two sources of truth for the same fact, divergent in practice.

## Decision

For each property that an enrichment source attaches to a corpus entity, apply this dispatch:

| target shape | model as |
|---|---|
| Pure type / class membership (`instance_of`, `subclass_of`, `type`, RDF `rdf:type`) | **Drop entirely.** Class labels already carry type. |
| QID **whose subject is a corpus entity** | **Edge** between two corpus nodes. |
| QID **whose subject is not a corpus entity** | **Property** on the source node: `{rel}_qids = "Q123;Q456"`, `{rel}_labels = "United Kingdom;Canada"`. |
| Scalar / literal (date, URL, number, coordinate) | **Property** on the source node, native typed value. |

Rule of thumb to read out loud: *"if we wouldn't independently care about this thing as a row in our database, it isn't a node."*

## Rationale

### Type membership doesn't need an edge

In a property graph, type is a **label**, not a relationship. `(:Person)` already says everything an `-[:INSTANCE_OF]->(humans)` edge would say, and with built-in index support. Persisting the edge form duplicates the fact and tempts query authors to write the wrong thing.

### Edges should imply traversability

A useful heuristic: an edge `A -[:R]-> B` exists in your graph because somebody will write a query that starts at `A`, hops `R`, and does something with `B`. If nobody will ever start at `B` (because `B` is just "United Kingdom" with no further structure in your corpus), then `B` should not be a node — its identifier and label are a property of `A`.

### Properties remain queryable

`MATCH (n:Organisation) WHERE 'Australia' IN split(n.located_in_country_labels, ';')` is only marginally more verbose than `MATCH (n:Organisation)-[:LOCATED_IN_COUNTRY]->(c:Place {wd_label: 'Australia'})`, and it doesn't require Australia to be a node. If you later decide Australia *should* be a node (e.g. you start tracking corpus entities **about Australia**), the migration is one-way: lift the property into a node, replace the property with an edge. The reverse migration — collapse a node into properties — is much more painful.

### Graph metrics stay meaningful

Centrality, degree, PageRank, clustering coefficient — all of these are computed across the node set. If 25% of your nodes are stubs, all of those numbers lie. Top-hub queries surface the type system, not the topology.

## Examples

### Do

```yaml
# A person enriched from Wikidata
node:
  id: Q9381
  labels: [Entity, Person]
  canonical: "Adam Smith"
  wd_label: "Adam Smith"
  wd_description: "Scottish moral philosopher and political economist (1723–1790)"
  citizen_of_qids: "Q161885"
  citizen_of_labels: "Kingdom of Great Britain"
  has_occupation_qids: "Q188094;Q15980158;Q4964182;Q36180;Q1622272"
  has_occupation_labels: "economist;non-fiction writer;philosopher;writer;university teacher"
  works_at_qids: "Q192775"
  works_at_labels: "University of Glasgow"

# No nodes minted for Q5, Q161885, Q188094, Q15980158, Q4964182, Q36180, Q1622272, Q192775.
# Adam Smith *is* a :Person (label) — no INSTANCE_OF edge needed.
```

### Don't

```yaml
# Anti-pattern: every Wikidata reference becomes a node
(:Person {id: 'Q9381', wd_label: 'Adam Smith'})
  -[:INSTANCE_OF]->  (:Stub {id: 'Q5', wd_label: 'human'})              # redundant w/ :Person label
  -[:CITIZEN_OF]->   (:Stub {id: 'Q161885', wd_label: 'Kingdom of GB'}) # not a corpus entity
  -[:HAS_OCCUPATION]->(:Stub {id: 'Q188094', wd_label: 'economist'})   # not a corpus entity
  ... × 5 more stubs ...
```

This is precisely the shape that floods centrality measures with the type system and makes "who matters" queries return Wikidata anchors instead of real people.

### Edges that survive

`Adam Smith` -[:CITIZEN_OF]-> `United Kingdom` — *if and only if* `United Kingdom` is itself a corpus entity (e.g. you're tracking UK-focused initiatives, the UK has its own page in your source documents). Otherwise: property on Adam Smith.

`Telstra` -[:HEADQUARTERED_IN]-> `Melbourne` — *if and only if* Melbourne is a corpus entity. The corpus we ran the pilot on tracked countries but not cities, so this became a property `headquartered_in_labels: "Melbourne"`.

## Implementation enforcement

### In the emit / build stage

When walking enrichment output:

```python
DROP_PROPS = {"instance_of", "subclass_of"}        # never modelled at all
PROP_TO_REL = {                                    # candidate edge types
    "country":               "LOCATED_IN_COUNTRY",
    "headquarters":          "HEADQUARTERED_IN",
    "industry":              "OPERATES_IN",
    "country_of_citizenship":"CITIZEN_OF",
    "occupation":            "HAS_OCCUPATION",
    "employer":              "WORKS_AT",
    # ...
}

# First pass: build corpus_qids set
corpus_qids = {row.qid for row in enriched if row.is_corpus_entity}

# Per-property dispatch:
for prop_name, target_qids in entity.properties.items():
    if prop_name in DROP_PROPS:
        continue
    rel = PROP_TO_REL.get(prop_name)
    if rel is None:
        continue                                   # not modelled as relationship
    for target_qid in target_qids:
        if target_qid in corpus_qids:
            emit_edge(entity.qid, rel, target_qid)
        else:
            node[f"{rel.lower()}_qids"].append(target_qid)
            node[f"{rel.lower()}_labels"].append(label_lookup[target_qid])
```

### In the Cypher load

The dual representation has one Cypher idiom for membership tests:

```cypher
// edge form (when the country IS a corpus node)
MATCH (n:Organisation)-[:LOCATED_IN_COUNTRY]->(:Place {wd_label: 'Australia'})
RETURN n;

// property form (when it isn't)
MATCH (n:Organisation)
WHERE 'Australia' IN split(coalesce(n.located_in_country_labels, ''), ';')
RETURN n;

// union of both (always-correct query):
MATCH (n:Organisation)
WHERE EXISTS {
        MATCH (n)-[:LOCATED_IN_COUNTRY]->(p:Place {wd_label: 'Australia'})
      }
   OR 'Australia' IN split(coalesce(n.located_in_country_labels, ''), ';')
RETURN n;
```

For queries that need to span both forms by default, define a Cypher procedure or a view; **don't** "fix" the dual representation by minting stubs.

### In tests

Add an invariant test that runs after every graph load:

```cypher
// No node should exist that isn't tagged with one of our known corpus classes.
MATCH (n) WHERE NOT any(label IN labels(n) WHERE label IN $corpus_classes)
RETURN count(n);  // must be 0
```

This catches stub regression if someone adds a new edge type but forgets the corpus-membership check.

## Anti-patterns to reject in review

| anti-pattern | why it's wrong |
|---|---|
| "We need an edge to `human` so we can find all people" | Use the `:Person` label. |
| "We need a node for every country so we can group by it" | Group by `country_labels` property; promote to nodes only when countries become first-class corpus entities. |
| "The Wikidata API returned this link, so let's preserve it." | The API returns *every* link a curator ever added. Most are uninteresting to your domain. The graph is a model of your domain, not a mirror of Wikidata. |
| "Stub nodes can be enriched later." | Stubs are not "yet to be enriched corpus entities" — they're *categorically* non-corpus. Enriching a stub doesn't make it a corpus entity; it makes the stub a more verbose stub. The path to becoming a corpus entity is being *referenced by source documents*, not being referenced by Wikidata. |

## When to revisit this norm

- The corpus broadens such that **what used to be reference data is now first-class**. (e.g. you start tracking countries as initiatives, not just labels.) Migrate by promoting the property into a node; update queries accordingly.
- A specific relationship type accumulates **so many demoted properties** that a slow `WHERE x IN split(...)` query dominates. Promote *those* targets into nodes selectively; the rule is per-target-class, not all-or-nothing.
- A downstream system (analytics, ML feature store) expects a particular schema. Adapt at the export boundary, not by restructuring the canonical graph.

## See also

- *Property graph vs RDF tradeoffs* — RDF triple stores conventionally model type as a triple. Property graphs model type as a label. The rule above is property-graph-native.
- *Schema evolution: properties → nodes* — the safe one-way migration when reference data becomes first-class.
- *Provenance modelling* — when multiple enrichment sources disagree on a label, the property pair becomes `{rel}_qids` + `{rel}_labels` + `{rel}_sources` (e.g. `wikidata;opencorporates`).
- *Graph viz centrality interpretation* — top-hub queries against a stub-free graph return the entities that matter; top-hub queries against a stub-laden graph return the type system.
