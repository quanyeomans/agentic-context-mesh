---
title: "Checklist"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

This checklist helps developers identify potential accessibility issues affecting their websites or applications. It's broken down into three sections of decreasing importance: A, B and C. Please check and address these issues in the order in which they appear.

For more detail on accessibility standards, please see [WCAG2.0 AA](https://www.w3.org/TR/WCAG20/)

 * A - Critical issues that will cause serious problems and/or stop most users of assistive technology from using the site
 * B - Issues that may cause problems or increased frustration for certain users
 * C - Minor issues that will cause problems or frustration for a small number of users

It is important to note, while B and C are noted as less critical, they are still required to be truly 508 compliant. This checklist should be used as a reference for development and is not a substitute for compliance checks by a section 508 coordinator.

##  A - Critical

1. [Site is keyboard accessible](../keyboard/)
    * All interactions can be accessed with a keyboard
2. [Site is free of keyboard traps](../keyboard/#keyboard-trap)
    * The keyboard focus is never trapped in a loop
4. [All `form` inputs have explicit labels](../forms/)
6. [All relevant images use an `img` tag](../images/)
5. [All images have `alt` attributes](../images/)
6. [Multimedia is tagged](../multimedia/)
    * All multimedia has appropriate captioning and audio description
7. [Text has sufficient color contrast](../color/)
    * All text has a contrast ratio of 4.5:1 with the background

## B - Less Critical

1. [Site never loses focus](../keyboard/)
    * Focus is always visible when moving through the page with the keyboard
2. [Tab order is logical](../keyboard/)
3. [Form instructions are associated with inputs](../forms/)
4. [Site doesn't timeout unexpectedly](../timeouts/)
    * Identify elements that may "timeout" and verify that the user can request more time
5. [Tables are coded properly](../tables/)
    * Tables have proper headers and column attributes
6. [Headings are nested properly](../headings/)
    * Heading elements are nested in a logical way

## C - Minor
1. [Frames are named](../iframes/)
    * All frames have a name element
2. [Flashing elements are compliant](../flashing/)
    * Elements that flash on screen do so at a rate of less than 3 Hz
3. [Language is set](../language/)
    * The language for the page is set
    * The language for sections on the page that differ from the site language are set
4. [CSS is not required to use the page](../css/)
    * The page makes sense with or without CSS
5. [Links are unique and contextual](../links/)
    * All links can be understood taken alone, e.g., 'Read more - about 508'
6. [Page titles are descriptive](../page-titles/)
7. [Required plugins are linked on the page](https://www.gsa.gov/website-information/accessibility-statement#:~:text=Accessibility%20aids%3A%20plug%2Dins%20and%20file%20viewers)

### Checklist for accessible files
We also need to create accessible files and assets. This includes slides, documents, forms, charts, and diagrams.

Use this [GSA only: Google tools checklist](https://docs.google.com/document/d/1DXiU7pBxMQogH5G4MFC79ki5xCMt1VlED5Mq-XUINmA/edit?usp=sharing) for creating more accessible files and assets.
