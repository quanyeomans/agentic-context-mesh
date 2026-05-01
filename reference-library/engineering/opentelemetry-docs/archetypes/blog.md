---
title: "{{ replaceRE \"[-_]\" \" \" .Name | title }}"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

With contributions from secondary-author-name-1, ..., and secondary-author-n.

## Top-level heading

Top-level headings start at **level 2**. This means, that your post should not
include `# headings` for top-level headings but `## headings` instead.

## Paragraphs

Wrap paragraph text at 80 characters, this helps make git diffs (which is line
based) more useful. If you don't want to bother with that, then just run the
markdown formatter (see below).

## Images

If you use images, make sure that your blog post is located in its own
directory. Put the images into the same directory.

If you have an image stored at `content/en/{{ .File.Dir }}imagename.png`, you
can reference them like the following:

![Provide a good image description for improved accessibility](imagename.png)

## Markdown formatter

Before submitting a new commit run the formatter over your file:

```sh
npm run format
```

Happy writing!

**Note:** If you view this page with the GitHub file viewer, you can safely
ignore the `Error in user YAML` at the top of this page.
