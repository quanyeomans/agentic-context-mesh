"""
Backwards compatibility shim — qmd_azure_embed.embed → mnemosyne.embed.embed.
"""
from mnemosyne.embed.embed import (  # noqa: F401
    encode_vector,
    build_hash_seq,
    batched,
    chunk_text,
    stage_embedding,
    flush_staging_to_vec,
    ensure_staging_table,
    run_embed,
)
