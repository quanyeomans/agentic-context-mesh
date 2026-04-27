---
title: "`prefer-blockquote-vs-docsy-alerts`"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

# `prefer-blockquote-vs-docsy-alerts`

Prefer [GFM blockquote alerts][style-guide-alerts] over the Docsy `alert`
shortcode.

## Rule design

This rule is kept simple and small: it scans the file line-wise
(`parser: 'none'`) using a regex to find plain or Markdown `alert` shortcode
calls like this:

```text
{{% alert … %}}
{{< alert … >}}
```

[Escaped shortcode-call delimiters][] like the following are ignored:

```text
{{</* alert … */>}}
{{%/* alert … */%}}
```

[style-guide-alerts]:
  https://opentelemetry.io/docs/contributing/style-guide/#alerts
[Escaped shortcode-call delimiters]:
  https://gohugo.io/content-management/syntax-highlighting/#escaping
