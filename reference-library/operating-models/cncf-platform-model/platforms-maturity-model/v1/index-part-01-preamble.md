---
title: "Platform Engineering Maturity Model"
source: CNCF TAG App Delivery (Platform Engineering)
source_url: https://github.com/cncf/tag-app-delivery
licence: Apache-2.0
domain: operating-models
subdomain: cncf-platform-model
date_added: 2026-04-25
---

<script>
window.onhashchange = function() {
  // get the fragment without the `#`
  const fragment = window.location.hash.substring(1)
  const found = Array.from(document.querySelectorAll('.nav-item'))
    .filter(el => el.textContent === decodeURI(fragment))
  if (!found) {
    return
  }

  if (found.length > 1) {
    console.warn(`Found multiple ` + "`.nav-item`s" + ` with the text ${parts[1]}, only opening the first one`)
  }

  found[0].click();
}
</script>