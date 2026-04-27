---
title: "Videos"
source: The Turing Way
source_url: https://github.com/the-turing-way/the-turing-way
licence: CC-BY-4.0
domain: data-and-analysis
subdomain: turing-way
date_added: 2026-04-25
---

(ch-style-videos)=
# Videos

## Hosted videos

If your video is hosted on a platform like YouTube or Vimeo you can use the [`iframe` directive](https://mystmd.org/guide/figures#youtube-videos) to embed it.
For example,

````
::: {iframe} https://www.youtube.com/embed/MdOS6tPq8fc
:width: 100%
:align: center
:label: example-video
How to build a Jupyter Book!
:::
````

renders as,

::: {iframe} https://www.youtube.com/embed/MdOS6tPq8fc
:width: 100%
:align: center
:label: example-video
How to build a Jupyter Book!
:::

and can be referenced with `[](#example-video)`, which when rendered will appear as: [](#example-video).

For youtube, the link formatting you need to use is `https://www.youtube.com/embed/` followed by the code at the end of the video URL (`MdOS6tPq8fc` for the above video example).
