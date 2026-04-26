---
title: "Agentic AI Projects"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# Agentic AI Projects

## Project 1 — Clone “OpenAI Study Mode”

**Goal:** Build a functional Study Mode clone that supports structured learning flows (explain, quiz, review), so students learn faster than the coffee cools. ☕️

**Implementation options (pick one to start, then level up):**

* **Google AI Studio** — For students not yet comfy with OpenAI Agents SDK or n8n.
* **n8n + Gemini + Lovable** — No/low-code orchestration with fast iteration.
* **OpenAI Agents SDK + Gemini + Chainlit** — Full-control, production-ready path.

**Deliverables:**

* Core flows: “Explain like I’m new,” flashcards/quiz, spaced-repetition review.
* Configurable study “modes” (beginner, practice, exam).
* README with setup, env, and usage; demo script or short walkthrough.
  Because if it isn’t demoed, did it even ship? 😉

**Reference:** [https://openai.com/index/chatgpt-study-mode/](https://openai.com/index/chatgpt-study-mode/)

---

## Project 2 — Advanced Accounts Agent (Xero)

**Goal:** Extend the Xero toolkit agent for accounting analysis, so your ledger insights show up before your meeting does. 😅

**Stack:** OpenAI Agents SDK + Gemini + Chainlit

**Enhancements:**

* Multi-tool agent: authentication, data retrieval, trial balance insights, anomaly flags.
* Natural-language queries → structured analysis (CSV summaries, charts later).
* Guardrails: rate limits, retries, and friendly error messages (your future self says thanks).

**Reference:** [https://github.com/XeroAPI/xero-agent-toolkit/tree/main/python/openai](https://github.com/XeroAPI/xero-agent-toolkit/tree/main/python/openai)

---

## Project 3 — Containerize Study Mode Clone

**Goal:** Dockerize the app with clean environments so “works on my machine” becomes “works everywhere.” 🧰

**Outcomes:**

* `Dockerfile`, `docker-compose.yml` (for local dev), `.env.example`.
* Health checks and minimal logging—because ops folks have feelings too.

---

## Project 4 — Deploy Study Mode on Kubernetes

**Goal:** Run the clone on K8s with a minimal, reproducible setup; pets to cattle, gently. 🐄

**Outcomes:**

* Manifests: Deployment, Service, Ingress (or Gateway), ConfigMap/Secret.
* Simple autoscaling (HPA) and rolling updates; basic observability hooks.
  Shipping green is cool; staying green is cooler. 🕶️

---

## Project 5 — Containerize Advanced Accounts Agent

**Goal:** Dockerize the accounts agent with predictable images and secure configs; no “mystery meat” containers. 🍔

**Outcomes:**

* Production `Dockerfile` (distroless/alpine), multi-stage build, non-root user.
* Secrets via env/Secret; linting in CI so you catch footguns before they catch you.

---

## Project 6 — Deploy Advanced Accounts Agent on Kubernetes

**Goal:** Production-style K8s deployment with the same goodness as Project 4, plus resource policies so it doesn’t eat the cluster for breakfast. 🥣

**Outcomes:**

* Deployment/Service/Ingress, HPA, PodDisruptionBudget, resource requests/limits.
* Basic tracing/logging so finance isn’t a black box (unless it’s a ledger joke).

---

## Project 7 — Study Mode on Kubernetes + Dapr

**Goal:** Add **Dapr** for service discovery, pub/sub, bindings, and secrets; microservices without the migraines. 🤕

**Outcomes:**

* Dapr sidecars for services, pub/sub for events (e.g., quiz-completed).
* Swappable components (e.g., Redis → NATS) without app change.

---

## Project 8 — Advanced Accounts Agent on Kubernetes + Dapr

**Goal:** Use Dapr building blocks (state store, pub/sub, secret store) for resilient accounting workflows; your audit trail will thank you later. 

**Outcomes:**

* Event-driven pipelines (fetch → analyze → notify), idempotent handlers.
* Centralized secrets and pluggable outputs (webhook, queue, email gateway).

---
## Project 9 — Distributed Python with Ray (Anyscale Tutorial)

Watch to Learn: https://www.youtube.com/watch?v=pX8OG4P9_V0

**Link:** [https://www.anyscale.com/blog/writing-your-first-distributed-python-application-with-ray](https://www.anyscale.com/blog/writing-your-first-distributed-python-application-with-ray)
**Goal:** Learn Ray’s task/actor model by building a small distributed Python app, then level it up toward production—so your laptop stops pretending it’s a data center. 

**Why this fits (and rocks):** Ray gives you simple primitives (tasks, actors, datasets) to scale CPU/GPU work without hand-rolling a distributed systems thesis—more “pip install” than “PhD install.” 

**Core outcomes (hands-on, fast):**

* Run the tutorial’s minimal Ray app locally with a multi-core Ray *cluster* (on one machine—like cosplay for servers).
* Implement tasks & actors, add retries/timeouts, and capture basic metrics/logs—observability so you don’t debug by vibes. 🔍
* Package the app as a tiny module with a clean CLI entry point—because future-you loves `python -m your_app`. 

**Stretch goals (choose your adventure):**

* **Ray Serve**: expose an HTTP endpoint for your compute graph—APIs that scale. 
* **Kubernetes with KubeRay**: deploy a RayCluster CRD on your local Rancher Desktop or cloud—autoscale like a champ.
* **Pipelines**: integrate with your Study Mode or Accounts Agent for heavy jobs (e.g., batch scoring, feature calc)—big muscles behind a friendly face. 
* **Dapr**: use pub/sub to trigger Ray jobs; experimental, but fun—like pineapple on pizza for microservices.
* **Agentic AI:** https://www.anyscale.com/blog/massively-parallel-agentic-simulations-with-ray

---

## Local Kubernetes Development

**Rancher (Desktop):** [https://www.rancher.com/](https://www.rancher.com/)
Spin up k8s locally with a friendly UI; finally, a dashboard that doesn’t look like cockpit controls from a 90s sci-fi film. 🚀

---

## Free, No-Card Kubernetes Playgrounds

A quick way to practice without setting your laptop on fire. 🔥 (Metaphorically.)

### Killercoda — Kubernetes Playgrounds

* About: [https://killercoda.com/about](https://killercoda.com/about)
* Playground: [https://killercoda.com/playgrounds/scenario/kubernetes](https://killercoda.com/playgrounds/scenario/kubernetes)
  Real in-browser k8s/k3s clusters (single/multi-node). Free-tier sessions last \~1 hour; restart as needed. Perfect for sprints that fit between snacks. 😋

### Play-with-Kubernetes (Docker)

* Workshop: [https://training.play-with-kubernetes.com/kubernetes-workshop/](https://training.play-with-kubernetes.com/kubernetes-workshop/)
  Classic browser lab from Docker; log in with Docker ID/GitHub. Sessions are short-lived—like that one tab you promise to read later. 📝

---

## Free Cloud Kubernetes (Credit Card Required)

Offers change—always check current terms before you swipe (responsibly, like a grown-up wizard). 🧙‍♂️

* **Civo Cloud — Most Economical**
  **\$250** credit for \~1 month from account creation; card required, no charges beyond credit.
  Sign-up: [https://www.civo.com/signup](https://www.civo.com/signup)
  Deploy, learn, delete—like origami, but cloudier. 🪁

* **DigitalOcean — Very Reasonable**
  **\$100** credit for 60 days (via referral/official link); card required.
  Referral: [https://m.do.co/c/8cce85e94a19](https://m.do.co/c/8cce85e94a19)
  Simple UX; droplets so friendly you’ll name them. 🧑‍🤝‍🧑

* **Alibaba Cloud — Free for 12 Months**
  **\$300** credit over 12 months; card required; Kubernetes also appears in “always free” resources.
  Start: [https://www.alibabacloud.com/](https://www.alibabacloud.com/) (signup flow may redirect)
  Stretch your experiments like taffy—sweet and long. 🍬

* **Microsoft Azure (AKS)**
  **\$200** credit for 30 days; card required. AKS often has highly economical options for AI/ML workloads.
  Free tier: [https://azure.microsoft.com/en-us/free/](https://azure.microsoft.com/en-us/free/)
  Big-league cloud without big-league drama (most days). ⚾️

---

## Free Ray Cloud

https://console.anyscale.com/register/ha

### Suggested Milestones (per project)

* **M1:** Feature complete (happy path)
* **M2:** Tests + docs + linting
* **M3:** Containerization + local compose
* **M4:** K8s manifests + CI deploy
* **M5:** Dapr integration (where applicable)
  Ship small, ship often—like sushi for software. 🍣

---

### Acceptance Checklist (use for 1–8)

* Clear README and `.env.example`
* Reproducible run: `make dev`, `docker compose up`, and `kubectl apply -f k8s/`
* Health checks, basic logs, graceful shutdown
* One-click demo script or Chainlit app
  If a new teammate can run it in under 10 minutes, you’ve nailed it like a pro carpenter. 🔨

---
