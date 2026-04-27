---
title: "methods homepage"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

<h1 class="visually-hidden">
      {{ title }}
    </h1>
    <p class="usa-intro usa-intro--methods no-print">A collection of tools to bring human-centered design into your project.</p>
  


{% for obj in method_categories %}
  {% comment %}
    methods_categories is a hash, so the object in the iteration is a key/values array.
    obj[0] is the key, which can be used as a slugified version of the category name.
    obj[1] is the category values object.
  {% endcomment %}
  {% assign category_slug = obj[0] %}
  {% assign category = obj[1] %}
  {% include "methods/homepage-category.html" category_slug: category_slug category: category %}
{% endfor %}
