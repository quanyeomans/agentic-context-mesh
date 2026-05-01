---
title: "Timeouts"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

If timeouts are used, you must give the user at least 20 seconds to easily request more time.

## Testing

1. Identify any timeouts on the page.
    * Contact the developer to find these.
2. Trigger the time out.
    * __If you're not able to request more time or the request lasts less than 20 seconds, it's a failure__.

## Examples

### Passes

Fill out this form

<form id='pForm' data-pa11y-ignore>
<label for='t1'>Field 1</label>&nbsp;
<label for='t2'>Field 2</label>&nbsp;
<label for='t3'>Field 3</label>&nbsp;
<label for='t4'>Field 4</label>&nbsp;
<label for='t5'>Field 5</label>&nbsp;
</form>

> This passes because a timeout does occur, but you are given more than 20 seconds to request more time.
