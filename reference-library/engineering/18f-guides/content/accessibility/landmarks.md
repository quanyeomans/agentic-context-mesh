---
title: "Landmarks"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

All elements on a page should be contained in a landmark element. This helps users of AT quickly navigate a page. HTML5 provides built in landmark elements such as `main`, `nav`, `aside`, `header`, `footer`. When using HTML5 elements, don't define role.

When using HTML4 use ARIA role on divs to define landmark elements such as `role="main"`.

## Testing

1. Identify content in the source code
2. Check that all rendered content is contained by a landmark element

## Examples

### HTML5

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <title>Sample page</title>
  </head>
  
    <header>
      <h1>Site Name</h1>
    </header>
    <nav>
      
        <li>Home</li>
        <li>[Other Page](./otherpage/)</li>
      
    </nav>
    <main>
      <article>Here's an article</article>
    </main>
    <footer>Footer</footer>
  
</html>
```

### HTML4

```html
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"
        "http://www.w3.org/TR/html4/strict.dtd">
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <title>Sample page</title>
  </head>
  
    
      <h1>Site Name</h1>
    
    
      
        <li>Home</li>
        <li>[Other Page](./otherpage/)</li>
      
    
    
      Here's an article
    
    Footer
  
</html>
```
