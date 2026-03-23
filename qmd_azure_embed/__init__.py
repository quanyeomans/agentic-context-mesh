"""
Backwards compatibility shim.
The qmd_azure_embed package has moved to mnemosyne.embed.
This shim keeps existing imports and the qmd-azure-embed CLI entry point working.
"""
from mnemosyne.embed import *  # noqa: F401, F403
