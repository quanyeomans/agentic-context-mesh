---
title: "OpenTelemetry"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

{{% blocks/cover image_anchor="top" height="max" color="primary" %}}


![OpenTelemetry](/img/logos/opentelemetry-horizontal-color.svg)
{.otel-logo}


{{% param description %}}
{.display-6}


- [আরও জানুন](docs/what-is-opentelemetry/)
- [Demo ট্রাই করুন](docs/demo/)


আপনার কাজের উপর ভিত্তি করে [ শুরু করুন ](docs/getting-started/)


- [Dev](docs/getting-started/dev/)
- [Ops](docs/getting-started/ops/)


{{% /blocks/cover %}}

{{% blocks/lead color="white" %}}

OpenTelemetry একটি API, SDK এবং টুলের সংগ্রহ। এটি ব্যবহার করে আপনি টেলিমেট্রি
ডেটা (মেট্রিকস, লগ এবং ট্রেস) ইনস্ট্রুমেন্ট (instrument), জেনারেট, কালেক্ট এবং
এক্সপোর্ট করতে পারেন, যা আপনার সফটওয়্যারের পারফরম্যান্স এবং আচরণ বিশ্লেষণে
সহায়তা করে।

> OpenTelemetry সাধারণভাবে [অনেক ভাষায়](docs/languages/)
> [এভেইলেবল (available)](/status/) এবং প্রোডাকশন ব্যবহারের জন্য উপযোগী।

{{% /blocks/lead %}}

{{% blocks/section color="dark" type="row" %}}

{{% blocks/feature icon="fas fa-chart-line" title="ট্রেস, মেট্রিকস, লগস" url="docs/concepts/observability-primer/" %}}

আপনার সার্ভিস এবং সফটওয়্যার থেকে টেলিমেট্রি তৈরি ও সংগ্রহ করুন, এবং এটি
বিশ্লেষণ টুলে পাঠান।

{{% /blocks/feature %}}

{{% blocks/feature icon="fas fa-magic" title="সহজ ইন্টিগ্রেশন ও ইন্সট্রুমেন্টেশন" %}}

OpenTelemetry অনেক জনপ্রিয় লাইব্রেরি ও ফ্রেমওয়ার্কের সাথে
[ইন্টিগ্রেট][integrates] করে এবং _code-based ও zero-code_
[ইন্সট্রুমেন্টেশন][instrumentation] সাপোর্ট করে।

[instrumentation]: /docs/concepts/instrumentation/
[integrates]: /ecosystem/integrations/

{{% /blocks/feature %}}

{{% blocks/feature icon="fab fa-github" title="ওপেন সোর্স, ভেন্ডর নিউট্রাল" %}}

১০০% বিনামূল্যে এবং ওপেন সোর্স, OpenTelemetry অবজারভেবিলিটির ক্ষেত্রে
[ইন্ডাস্ট্রি লিডার্স][industry leaders] দ্বারা [গৃহীত][adopted] এবং সমর্থিত।

[adopted]: /ecosystem/adopters/
[industry leaders]: /ecosystem/vendors/

{{% /blocks/feature %}}

{{% /blocks/section %}}

{{% blocks/section color="secondary" type="cncf" %}}

**OpenTelemetry একটি [CNCF][] [ইনকিউবেটিং][incubating] প্রজেক্ট**। এটি
OpenTracing এবং OpenCensus প্রকল্পের একীভূতকরণের মাধ্যমে গঠিত।

[![CNCF logo][]][cncf]

[cncf]: https://cncf.io
[cncf logo]: /img/logos/cncf-white.svg
[incubating]: https://www.cncf.io/projects/

{{% /blocks/section %}}
