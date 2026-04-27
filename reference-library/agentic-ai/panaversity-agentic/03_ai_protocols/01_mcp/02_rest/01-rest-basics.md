---
title: "What is REST?"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# What is REST?

> REST is a **way to build web applications** so that **computers can talk to each other over the internet easily and in an organized way**.

---

## 🔍 Is REST a technology?

**No**, REST is **not** a:

* Programming language
* Software
* Tool
* Protocol like HTTP or FTP

✅ REST is a **style or design pattern**—a **set of rules** for **how web services should work**.

---

## 📦 Real-Life Analogy

Imagine you're using a **food delivery app**:

* You search for food
* You add items to cart
* You place an order
* You check order status

All these actions are done by talking to the **backend server** using **RESTful APIs**.

Each thing (food, cart, order) is a **resource**, and you're interacting with **representations** of them.

---

## 🔑 REST Means...

| Term                 | Simple Meaning                                                                    |
| -------------------- | --------------------------------------------------------------------------------- |
| **Representational** | You don’t access the real object—you access a **copy** (e.g., JSON or HTML of it) |
| **State**            | The **data** or **condition** of something (like your cart contents)              |
| **Transfer**         | Moving that data between **client** (you) and **server** (app backend)            |

---

## 🖼️ How REST Works – Step-by-Step

1. **Resources** (like users, products, orders) are given **URLs** (like `api/products/1`)
2. The **client** (browser or app) sends an HTTP request like:

   * `GET` → to read
   * `POST` → to add
   * `PUT` → to update
   * `DELETE` → to delete
3. The **server** sends back a **representation** (usually in JSON or XML)

---

## 🧱 Example: REST in a Shopping App

| Action          | HTTP Method | URL           | What Happens              |
| --------------- | ----------- | ------------- | ------------------------- |
| See products    | `GET`       | `/products`   | Get list of all products  |
| See one product | `GET`       | `/products/5` | Get product with ID 5     |
| Add a product   | `POST`      | `/products`   | Add a new product         |
| Update product  | `PUT`       | `/products/5` | Replace product with ID 5 |
| Delete product  | `DELETE`    | `/products/5` | Remove product with ID 5  |

---

## REST Has 6 Simple Rules (called Constraints)

1. **Client-Server**: Separate frontend (browser/app) from backend (server).
2. **Stateless**: Each request stands alone. Server doesn’t remember past requests.
3. **Cacheable**: Responses can be stored to reduce load and speed things up.
4. **Uniform Interface**: All resources are accessed using the same method (like GET/POST).
5. **Layered System**: You can have layers like proxies or load balancers in between.
6. **Code on Demand (optional)**: Server can send code (like JavaScript) to the client to run.

---

## ✅ Summary (In Easy Words)

| Term               | Meaning                                           |
| ------------------ | ------------------------------------------------- |
| **REST**           | A design style for creating web services          |
| **Not a protocol** | It uses HTTP but it's not HTTP itself             |
| **Resource**       | Any piece of data (user, product, etc.)           |
| **Representation** | A copy of the data (like JSON) sent to the client |
| **Stateless**      | Every request is treated like a brand-new one     |

---

## What Does HATEOAS Mean?

HATEOAS stands for:

> **Hypermedia As The Engine Of Application State**

🟢 It's a **part of REST** that says:

> The **server should guide the client** by sending links in its responses, so the client knows **what to do next** — **just like how you browse a website**.

---

## 📖 Real-Life Analogy: Browsing a Website

Think about how you use a website:

1. You land on the **homepage**
2. You see **links** like "About", "Products", "Contact"
3. You **click a link** to go to the next page
4. You don’t need to **memorize URLs** — you just follow the links provided

✅ That’s **exactly what HATEOAS means for computers talking via REST APIs**.

---

## 🤖 In REST APIs (Without HATEOAS vs With HATEOAS)

### ❌ Without HATEOAS:

The client must already know:

* All the URLs (`/users`, `/orders`, `/cart`)
* What it’s allowed to do

It’s like someone saying:

> “Go to `store.com/api/products`, then `store.com/api/cart`, and then `store.com/api/checkout`”
> Even before you start.

This is **fragile**. If URLs change, the client breaks.

---

### ✅ With HATEOAS:

You only start with **one known URL**, like:

```
GET /api
```

And the server replies with:

```json
{
  "links": {
    "products": "/api/products",
    "cart": "/api/cart",
    "checkout": "/api/checkout"
  }
}
```

🔗 Now the **client follows these links**, like a **map**.

> It’s like the **server says: “Here’s what you can do next!”**

---

## 🔄 Example in REST API (JSON)

Let’s say you get a user profile:

```json
{
  "id": 1,
  "name": "Wania",
  "links": {
    "self": "/api/users/1",
    "update": "/api/users/1",
    "delete": "/api/users/1",
    "orders": "/api/users/1/orders"
  }
}
```

✅ Now the client knows:

* How to view this user
* How to update/delete the user
* How to get their orders

And it doesn’t need to memorize anything!

---

## 🔑 Why is HATEOAS Useful?

| Benefit         | Simple Explanation                                     |
| --------------- | ------------------------------------------------------ |
| 🔄 Flexible     | Server can change URLs without breaking the client     |
| 🧭 Discoverable | Clients don’t need a full guide or list of all actions |
| 🔐 Secure       | Server controls what links/actions to show             |
| 🔧 Evolvable    | New features can be added without updating the client  |

---

## 📝 Summary

| Term                         | Simple Meaning                               |
| ---------------------------- | -------------------------------------------- |
| **HATEOAS**                  | Server tells the client what it can do next  |
| **Hypermedia**               | Links (like in websites) in the API response |
| **Client starts with 1 URL** | And discovers others by following links      |
| **Less breakable**           | Client doesn’t rely on hardcoded URLs        |

---

## 📦 Think of HATEOAS like this:

> **The server is a tour guide** — it doesn’t give you the whole city map at once, but at every step, it tells you what places you can go next.

---
Sure! Let’s explain **Idempotence** in **very simple and easy words**, so you can understand it without any confusion. We'll also use real-life examples. 😊

---

## What is Idempotence?

> **Idempotence** means:
> “Doing the same action **many times** has the **same effect** as doing it **once**.”

---

### 🧃 Real-Life Example: Light Switch

Imagine you walk into a room and **turn off the light**:

* 🔁 If you turn it off **once**, the light goes off ✅
* 🔁 If you turn it off **5 times**, it's still off ✅

➡️ Same result, no matter how many times you repeat the action
✅ This is **idempotent**

---

### 🍽️ Another Example: Canceling a food order

Let’s say you cancel your food order:

* You cancel once → Order is canceled
* You cancel again → It’s **still** canceled, nothing changes

✅ **Idempotent**

But now imagine you **place an order**:

* You press “Place Order” once → 1 pizza ordered 🍕
* You press it 3 times → 3 pizzas ordered 😱

❌ **Not idempotent**

---

## 🌐 In HTTP (Web Terms)

Here’s how **HTTP methods** behave regarding idempotence:

---

### ✅ **Idempotent Methods**

| Method      | What It Does         | Why It's Idempotent                                                            |
| ----------- | -------------------- | ------------------------------------------------------------------------------ |
| **GET**     | Get data (read only) | Getting the same data many times doesn’t change it                             |
| **HEAD**    | Get only headers     | Like GET, but no body – doesn’t change anything                                |
| **OPTIONS** | Ask what’s allowed   | Just asks a question, doesn’t modify anything                                  |
| **PUT**     | Replace data         | Sending the same data multiple times replaces it again – result stays the same |
| **DELETE**  | Delete resource      | Deleting once or 10 times = still deleted                                      |

✅ Example:

```http
PUT /users/1
{
  "name": "Wania"
}
```

Sending this 100 times is the same as sending it once.

---

### ❌ **Non-idempotent Methods**

| Method    | What It Does        | Why It’s Not Idempotent                          |
| --------- | ------------------- | ------------------------------------------------ |
| **POST**  | Create a new item   | Each request usually creates a **new** item      |
| **PATCH** | Modify part of data | Can be idempotent, but only if handled carefully |

🚫 Example:

```http
POST /users
{
  "name": "Wania"
}
```

Sending this 3 times might create **3 users** → **Not idempotent**

---

## ✅ Summary Table

| Method  | Idempotent?  | Why?                           |
| ------- | ------------ | ------------------------------ |
| GET     | ✅ Yes        | Reads only                     |
| HEAD    | ✅ Yes        | Reads headers                  |
| OPTIONS | ✅ Yes        | Just asks, doesn’t do          |
| PUT     | ✅ Yes        | Replaces existing data         |
| DELETE  | ✅ Yes        | Still deleted after many tries |
| POST    | ❌ No         | Adds new items                 |
| PATCH   | ⚠️ Sometimes | Depends on how it's used       |

---

## 📦 Final Tip

> If **repeating a request doesn’t change the result**, it’s **idempotent**.
> If it **adds more data or creates new things**, it’s **not idempotent**.
