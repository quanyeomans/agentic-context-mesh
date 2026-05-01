---
title: "RAG with Astra DB and Cassandra"
source: OpenAI Cookbook
source_url: https://github.com/openai/openai-cookbook
licence: MIT
domain: agentic-ai
subdomain: openai-cookbook
date_added: 2026-04-25
---

# RAG with Astra DB and Cassandra

The demos in this directory show how to use the Vector
Search capabilities available today in **DataStax Astra DB**, a serverless
Database-as-a-Service built on Apache Cassandra®.

These example notebooks demonstrate implementation of
the same GenAI standard RAG workload with different libraries and APIs.

To use [Astra DB](https://docs.datastax.com/en/astra/home/astra.html)
with its HTTP API interface, head to the "AstraPy" notebook (`astrapy`
is the Python client to interact with the database).

If you prefer CQL access to the database (either with
[Astra DB](https://docs.datastax.com/en/astra-serverless/docs/vector-search/overview.html)
or a Cassandra cluster
[supporting vector search](https://cassandra.apache.org/doc/trunk/cassandra/vector-search/overview.html)),
check the "CQL" or "CassIO" notebooks -- they differ in the level of abstraction you get to work at.

If you want to know more about Astra DB and its Vector Search capabilities,
head over to [datastax.com](https://docs.datastax.com/en/astra/home/astra.html).

### Example notebooks

The following examples show how easily OpenAI and DataStax Astra DB can
work together to power vector-based AI applications. You can run them either
with your local Jupyter engine or as Colab notebooks:

| Use case | Target database | Framework | Notebook | Google Colab |
| -------- | --------------- | --------- | -------- | ------------ |
| Search/generate quotes | Astra DB | AstraPy | [Notebook](./Philosophical_Quotes_AstraPy.ipynb) |  |
| Search/generate quotes | Cassandra / Astra DB through CQL | CassIO | [Notebook](./Philosophical_Quotes_cassIO.ipynb) |  |
| Search/generate quotes | Cassandra / Astra DB through CQL | Plain Cassandra language | [Notebook](./Philosophical_Quotes_CQL.ipynb) |  |

### Vector similarity, visual representation

![3_vector_space](https://user-images.githubusercontent.com/14221764/262321363-c8c625c1-8be9-450e-8c68-b1ed518f990d.png)
