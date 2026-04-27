---
title: "Inline frames"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

When using `iframe`s, it’s important that all content contained in them is accessible.

## Testing

1. Identify all `iframe`s on a page.
2. Using the keyboard, navigate to each `iframe` to ensure content is accessible.
3. Check the `title` or `name` attribute of each `iframe` for a description of the content.

## Examples


### Failures


```html

```

> This `iframe` doesn’t have a `title` or `name`.


```html

```

> This `name` isn’t correct.

### Passes


```html

```

> Correct `title` is provided. This would also pass if this information was in a `name` attribute.
