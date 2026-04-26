---
title: "Flashing"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

Flashing is generally a bad idea. It can cause all sorts of issues, from seizures to motion sickness. If you absolutely must have a flashing element there are a few things to consider.
## Testing

* Failure at any step constitutes a 508 compliance issue

1. Look for elements that contain flashing
    * Scrolling or blinking text
    * Scrolling or blinking page elements
    * Videos that contain flickering or flashing
    * GIFs that contain flickering or flashing
2. Check if you can determine the frequency of "flashing."
3. Check that the rate of flashing is less than 3 Hz (3 times a second), or scroll delay is set to >= 400.

## Examples

### Fails
Click to see non-compliant flashing
This text is blinking

> ___Failure:___ This blinking text fails because the rate of flashing can't be determined and its greater than 3 Hz.
