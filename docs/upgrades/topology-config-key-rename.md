# The `topology_v2` config setting is now just `topology`

> **Do you need to do anything? No.** Your existing setup keeps working exactly as it does today. There is nothing to change, and nothing breaks.

## What changed

The block in your `kairix.config.yaml` that lists your connectors, credentials, collections, and scope profiles used to be named `topology_v2`. It is now named `topology`. The `v2` was a leftover from when this was a new, second version of the setting — it has been the only version for a long time, so the name is now simpler.

```yaml
# Before
topology_v2:
  connectors: [...]
  collections: [...]

# After
topology:
  connectors: [...]
  collections: [...]
```

## Why you don't need to do anything

kairix reads the old name automatically. If your config file still says `topology_v2`, kairix treats it as `topology` the moment it reads the file — in memory only. It never rewrites your file, so this is safe even on locked-down, read-only setups. Your connectors, collections, and scopes all keep loading the same way.

You will only see the difference the next time you connect a source through the setup wizard: from then on, kairix writes the new `topology` name into your config. If your file ends up with both names, that is fine — kairix always uses `topology` and ignores the old block.

## If you want to tidy up (optional)

There is no need, but if you like a clean config file you can rename the key by hand:

1. Open your `kairix.config.yaml` (or your overlay file).
2. Change the top-level `topology_v2:` line to `topology:`.
3. Save and restart kairix.

That's the whole change. Everything under the block stays exactly the same.
