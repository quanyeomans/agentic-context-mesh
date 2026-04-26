## Handling Large Unchanged Code Blocks:

- If there's a large contiguous block of unchanged code you may use the comment `// ... keep existing code` (in English) for large unchanged code sections.
- Only use `// ... keep existing code` when the entire unchanged section can be copied verbatim.
- The comment must contain the exact string "... keep existing code" because a regex will look for this specific pattern. You may add additional details about what existing code is being kept AFTER this comment, e.g. `// ... keep existing code (definitions of the functions A and B)`.
- If any part of the code needs to be modified, write it out explicitly.