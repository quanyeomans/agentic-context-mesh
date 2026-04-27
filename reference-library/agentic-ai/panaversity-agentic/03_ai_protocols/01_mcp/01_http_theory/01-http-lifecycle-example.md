---
title: "🔧 Hands-on Example"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# 🔧 Hands-on Example

Let’s simulate **you visiting a webpage**.
We’ll break it down as if you're using a browser (client), and the website is hosted on a server.

---

### Scenario: You open `https://example.com/hello`

---

### 📤 HTTP Request (sent from browser to server)

```http
GET /hello HTTP/1.1
Host: example.com
User-Agent: Chrome/123.0
Accept: text/html
```

This means:

* `GET`: You are requesting to **get** a page
* `/hello`: This is the path you're trying to access on the site
* `Host`: You're talking to `example.com`
* `User-Agent`: Your browser type
* `Accept`: You prefer to receive `text/html` (webpages)

---

### 📥 HTTP Response (sent from server back to browser)

```http
HTTP/1.1 200 OK
Content-Type: text/html
Content-Length: 38

<html>Hello, World!</html>
```

This means:

* `200 OK`: Success, page found
* `Content-Type`: It's sending back HTML content
* `Content-Length`: The size of the page
* The body: Actual webpage content

Your browser sees this and **shows you the page** with `Hello, World!`.

---

## Simple Visual Diagram

Here's how the **Request-Response Cycle** works:

```
┌────────────┐       HTTP Request        ┌───────────────┐
│  Browser   │ ───────────────────────▶ │    Server     │
│ (Client)   │                          │ (example.com) │
└────────────┘                          └───────────────┘
     ▲                                        │
     │       HTTP Response (HTML Page)        ▼
┌────────────┐ ◀────────────────────── ┌───────────────┐
│  You see   │                          │    Sends:     │
│ "Hello 🌍" │                          │   200 OK      │
└────────────┘                          │ HTML Page     │
                                       └───────────────┘
```

---

## Try It Yourself with `curl`

If you have a terminal (like on Linux, Mac, or Git Bash on Windows), try this command:

```bash
curl -v https://example.com
```

It will show you:

* The full request
* The full response
* The HTML content received

---

## Key Takeaway

* HTTP is a **conversation** between a **browser (client)** and a **website (server)**.
* You send a **request** (asking for a page or data), and the server sends a **response** (content or error).
* This happens every time you load any website.
