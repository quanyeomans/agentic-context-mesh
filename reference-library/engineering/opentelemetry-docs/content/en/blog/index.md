---
title: "Blog"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

<script>
    document.addEventListener("DOMContentLoaded", function () {
        if (window.location.pathname.includes('/page/')) return;

        // Open the sidebar year-groups for the current and previous years
        var currentYear = new Date().getFullYear();
        var yearsToCheck = [currentYear, currentYear - 1];

        yearsToCheck.forEach(function(year) {
            var checkbox = document.getElementById("m-blog" + year + "-check");
            if (checkbox) checkbox.checked = true;
        });
    });
</script>
