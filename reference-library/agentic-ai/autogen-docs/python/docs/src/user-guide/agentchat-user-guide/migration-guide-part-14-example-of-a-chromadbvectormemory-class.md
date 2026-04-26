# example of a ChromaDBVectorMemory class
chroma_user_memory = ChromaDBVectorMemory(
    config=PersistentChromaDBVectorMemoryConfig(
        collection_name="preferences",
        persistence_path=os.path.join(str(Path.home()), ".chromadb_autogen"),
        k=2,  # Return top  k results
        score_threshold=0.4,  # Minimum similarity score
    )
)