---
title: "Step 0: A2A Transport Layer"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# Step 0: [A2A Transport Layer](https://a2a-protocol.org/latest/specification/#31-transport-layer-requirements)

**Understand A2A transport layer specifications, message formats, and protocol requirements before implementing agents**

> **🎯 Learning Objective**: Gain deep understanding of A2A Protocol transport layer specifications, including HTTPS requirements, supported transport protocols (JSON-RPC 2.0, gRPC, HTTP+JSON), and message format standards.

## 🧠 Learning Sciences Foundation

### **Transport Specification Literacy**
- **Protocol Standards Comprehension**: Understanding formal transport layer specifications and requirements
- **Message Format Analysis**: Parsing protocol-specific message structures and constraints
- **Implementation Planning**: Translating transport specifications into practical development decisions

### **Technical Reading and Analysis**
- **Specification Deep Dive**: Analyzing official A2A transport layer documentation
- **Constraint Recognition**: Identifying mandatory transport requirements vs optional features
- **Protocol Comparison**: Understanding trade-offs between different A2A transport options

## 🎯 What You'll Learn

### **Core Transport Specifications**
- **HTTPS Requirements** - Mandatory encryption and security constraints
- **JSON-RPC 2.0 Transport** - Request/response structure and error handling
- **gRPC Transport** - High-performance binary protocol specifications  
- **HTTP+JSON Transport** - RESTful patterns and message formats

### **Message Format Standards**
- Analyze transport-specific message structures
- Understand protocol headers and metadata requirements
- Compare message efficiency across transport types
- Map transport choices to implementation scenarios

### **Implementation Guidance**
- How transport choice affects agent performance and security
- When to use JSON-RPC vs gRPC vs HTTP+JSON
- Transport-specific compliance requirements for production

## 📋 Prerequisites

✅ **Basic Understanding**: HTTP/HTTPS protocols and JSON data formats  
✅ **Protocol Awareness**: Basic knowledge of REST APIs and web standards  
✅ **Reading Skills**: Ability to parse technical specification documents  
✅ **No Coding Required**: This step focuses on specification understanding  

## 🎯 Success Criteria

By the end of this step, you'll have:

### **Transport Specification Mastery**
- [ ] Complete understanding of A2A HTTPS requirements and security mandates
- [ ] Detailed knowledge of JSON-RPC 2.0 transport specifications
- [ ] Comprehensive grasp of gRPC transport protocol features
- [ ] Clear understanding of HTTP+JSON transport patterns

### **Implementation Readiness**
- [ ] Ability to choose optimal transport protocol for different scenarios
- [ ] Understanding of message format requirements per transport type
- [ ] Knowledge of transport-specific performance implications
- [ ] Clear mapping from transport specs to implementation decisions

### **Strategic Transport Knowledge**
- [ ] Understanding of when to use JSON-RPC vs gRPC vs HTTP+JSON
- [ ] Knowledge of transport security implications and requirements
- [ ] Awareness of scalability considerations per transport type

## 🌐 The A2A Transport Architecture

### HTTPS-Only Foundation

**Critical Requirement**: All A2A communication MUST occur over HTTPS in production.

- 🔒 **TLS 1.3+ Recommended**: Modern TLS configurations with strong cipher suites
- 🛡️ **Certificate Validation**: Clients SHOULD verify server TLS certificates against trusted CAs
- 🏢 **Enterprise Ready**: Built on established web security practices

### Three Equal Transport Protocols

A2A defines three core transport protocols - **all are considered equal in status**:

```
🔧 JSON-RPC 2.0 → Standard protocol (most common)
⚡ gRPC over HTTP/2 → High-performance binary protocol  
🌐 HTTP+JSON/REST → REST-style JSON over HTTP
```

**Key Principle**: Agents MUST implement **at least one** transport but MAY implement any combination.

### Transport Comparison Table

| Feature | JSON-RPC 2.0 | gRPC over HTTP/2 | HTTP+JSON/REST |
|---------|---------------|------------------|----------------|
| **A2A Status** | Standard | Equal Status | Equal Status |
| **Compliance** | Core requirement | Optional | Optional |
| **Complexity** | Medium | Advanced | Simple |
| **Performance** | Excellent | Excellent | Good |
| **Streaming** | SSE (Server-Sent Events) | Native bidirectional | SSE (similar to JSON-RPC) |
| **Type Safety** | JSON Schema | Protocol Buffers | JSON Schema |
| **Tooling** | Universal HTTP | Specialized gRPC | Universal HTTP |
| **Learning Curve** | Medium | Steep | Easy |
| **Method Names** | `message/send` | `SendMessage()` | `POST /v1/message:send` |
| **Error Handling** | JSON-RPC errors | gRPC status codes | HTTP status codes |
| **Best For** | Production A2A | High-performance | Simple integrations |

## 🔄 Transport Protocol Details

### 1. JSON-RPC 2.0 over HTTP (Standard A2A)

**What it is**: The standard A2A transport protocol using JSON-RPC 2.0 specification over HTTPS.

**Compliance**: Agents MUST implement at least one transport - JSON-RPC 2.0 is the most common choice.

**Message Structure**:
```json
POST /a2a/v1
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "text",
          "text": "Schedule a meeting with John for tomorrow at 3 PM"
        }
      ],
      "messageId": "uuid-123"
    }
  },
  "id": "req-123"
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "id": "task-456",
    "contextId": "ctx-789", 
    "status": {
      "state": "completed"
    },
    "artifacts": [
      {
        "artifactId": "artifact-1",
        "parts": [
          {
            "kind": "text",
            "text": "Meeting scheduled with John for tomorrow at 3 PM"
          }
        ]
      }
    ],
    "kind": "task"
  },
  "id": "req-123"
}
```

**Core JSON-RPC Methods**:
- `message/send` - Send a single message to start or continue a task
- `message/stream` - Send message with Server-Sent Events streaming  
- `tasks/get` - Get task status and results
- `tasks/cancel` - Cancel a running task
- `tasks/resubscribe` - Resume streaming for existing task
- `agent/getAuthenticatedExtendedCard` - Get detailed agent card

**When to use**:
- ✅ **Standard A2A implementations** (recommended)
- ✅ Production agent-to-agent communication
- ✅ When you need full A2A protocol compliance
- ✅ Server-Sent Events for streaming

### 2. gRPC over HTTP/2 (High Performance)

**What it is**: High-performance binary protocol with Protocol Buffers and native streaming.

**Service Definition** (Protocol Buffers):
```protobuf
service A2AService {
  rpc SendMessage(SendMessageRequest) returns (SendMessageResponse);
  rpc SendStreamingMessage(SendMessageRequest) returns (stream SendStreamingMessageResponse);
  rpc GetTask(GetTaskRequest) returns (GetTaskResponse);
  rpc CancelTask(CancelTaskRequest) returns (CancelTaskResponse);
}

message SendMessageRequest {
  Message message = 1;
  MessageSendConfiguration configuration = 2;
}

message Message {
  string role = 1;
  repeated Part parts = 2;
  string message_id = 3 [json_name = "messageId"];
  string task_id = 4 [json_name = "taskId"];
  string context_id = 5 [json_name = "contextId"];
}
```

**Method Mapping**:
- `SendMessage()` ↔ `message/send` 
- `SendStreamingMessage()` ↔ `message/stream`
- `GetTask()` ↔ `tasks/get`
- `CancelTask()` ↔ `tasks/cancel`

**When to use**:
- ✅ High-performance agent communication
- ✅ Real-time streaming requirements (bidirectional)
- ✅ Type-safe agent interfaces
- ✅ Microservice architectures
- ⚠️ Requires specialized tooling and setup

### 3. HTTP+JSON/REST (Simple Integration)

**What it is**: REST-style HTTP with JSON payloads, using standard HTTP methods and URL patterns.

**Message Structure**:
```bash
POST /v1/message:send
Content-Type: application/json

{
  "message": {
    "role": "user",
    "parts": [
      {
        "kind": "text", 
        "text": "Check my calendar for tomorrow"
      }
    ],
    "messageId": "uuid-456"
  },
  "configuration": {
    "blocking": true
  }
}
```

**Response**:
```json
{
  "id": "task-789",
  "contextId": "ctx-012",
  "status": {
    "state": "completed"
  },
  "artifacts": [
    {
      "artifactId": "artifact-2",
      "parts": [
        {
          "kind": "text",
          "text": "You have 2 meetings tomorrow: 9 AM standup and 2 PM review"
        }
      ]
    }
  ],
  "kind": "task"
}
```

**REST URL Patterns**:
- `POST /v1/message:send` - Send message
- `POST /v1/message:stream` - Send with streaming
- `GET /v1/tasks/{id}` - Get task
- `POST /v1/tasks/{id}:cancel` - Cancel task
- `GET /v1/card` - Get agent card

**When to use**:
- ✅ Simple integrations and prototyping
- ✅ When REST patterns are preferred
- ✅ Standard HTTP tooling
- ✅ Server-Sent Events for streaming (similar to JSON-RPC)

## 🎯 A2A Compliance & Method Mapping

### Compliance Requirements

**Agent Compliance**: To be A2A-compliant, agents MUST:

1. **Transport Support**: Implement at least one of the three core transports
2. **Core Methods**: Support `message/send`, `tasks/get`, `tasks/cancel` 
3. **Agent Card**: Provide valid AgentCard with transport declarations
4. **HTTPS Only**: Use HTTPS in production with strong TLS

**Multi-Transport Agents** MUST provide:
- **Functional Equivalence**: Same operations across all supported transports
- **Consistent Behavior**: Semantically equivalent results
- **Same Error Handling**: Consistent error codes and messages

### Method Mapping Across Transports

| A2A Operation | JSON-RPC 2.0 | gRPC Method | REST Endpoint |
|---------------|---------------|-------------|---------------|
| **Send Message** | `message/send` | `SendMessage()` | `POST /v1/message:send` |
| **Stream Messages** | `message/stream` | `SendStreamingMessage()` | `POST /v1/message:stream` |
| **Get Task** | `tasks/get` | `GetTask()` | `GET /v1/tasks/{id}` |
| **List Tasks** | N/A | `ListTask()` | `GET /v1/tasks` |
| **Cancel Task** | `tasks/cancel` | `CancelTask()` | `POST /v1/tasks/{id}:cancel` |
| **Resubscribe** | `tasks/resubscribe` | `TaskSubscription()` | `POST /v1/tasks/{id}:subscribe` |
| **Set Push Config** | `tasks/pushNotificationConfig/set` | `CreateTaskPushNotification()` | `POST /v1/tasks/{id}/pushNotificationConfigs` |
| **Get Agent Card** | `agent/getAuthenticatedExtendedCard` | `GetAgentCard()` | `GET /v1/card` |

### Agent Card Transport Declaration

Agents MUST declare supported transports in their Agent Card:

```json
{
  "name": "Example Agent",
  "url": "https://agent.example.com/a2a/v1",
  "preferredTransport": "JSONRPC",
  "additionalInterfaces": [
    {
      "url": "https://agent.example.com/a2a/v1", 
      "transport": "JSONRPC"
    },
    {
      "url": "https://agent.example.com/a2a/grpc",
      "transport": "GRPC"
    },
    {
      "url": "https://agent.example.com/a2a/rest",
      "transport": "HTTP+JSON"
    }
  ],
  "capabilities": {
    "streaming": true,
    "pushNotifications": true
  }
}
```

**Transport Selection Rules**:
1. **Prefer declared preference**: Use `preferredTransport` if client supports it
2. **Fallback selection**: Choose from `additionalInterfaces` if preferred not supported
3. **URL-transport matching**: Use correct URL for selected transport protocol
4. **Graceful degradation**: Implement fallback logic for transport failures

## 🏗️ Transport Architecture Patterns

### Single Transport (Minimum Compliance)

```
┌─────────────┐    JSON-RPC 2.0    ┌─────────────┐
│ A2A Client  │◄─────────────────►│ A2A Server  │
│ Agent       │    HTTPS only      │ Agent       │
└─────────────┘                    └─────────────┘
```

**Requirements**: HTTPS + at least one transport + core methods

### Dual Transport (Production Flexibility)

```
┌─────────────┐    JSON-RPC 2.0     ┌─────────────┐
│ A2A Client  │◄──────────────────►│ A2A Server  │
│ Agent       │    HTTPS/1.1        │ Agent       │
│             │                     │             │
│             │     gRPC            │             │
│             │◄──────────────────►│             │
└─────────────┘    HTTP/2 + TLS     └─────────────┘
```

**Benefits**: Performance options + client compatibility

### Multi Transport (Enterprise)

```
              HTTP+JSON/REST (simple clients)
         ┌─────────────────────────────────────┐
         │                                     │
         ▼                                     ▼
┌─────────────┐                      ┌─────────────┐
│ A2A Client  │    JSON-RPC 2.0      │ A2A Server  │
│ Agent       │◄───────────────────►│ Agent       │
│             │    (standard)        │             │
│             │                      │             │
│             │      gRPC            │             │
│             │◄───────────────────►│             │
└─────────────┘   (high-perf)        └─────────────┘
```

**Best Practice**: All transports available, clients choose based on needs

## 🔧 Choosing the Right Transport

### Decision Framework

```
📋 A2A Transport Selection Checklist:

🚀 Starting A2A Development?
   → Use JSON-RPC 2.0 (standard, best supported)

⚡ Need Maximum Performance?
   → Use gRPC for latency-critical communication

🌐 Simple Integration Required?
   → Use HTTP+JSON/REST for familiar patterns

🏢 Production A2A System?
   → Implement JSON-RPC 2.0 + optional gRPC

📱 Client Compatibility Priority?
   → JSON-RPC 2.0 has best universal support

🔄 Heavy Streaming Requirements?
   → gRPC for bidirectional streams, JSON-RPC for SSE

�️ Enterprise Security Focus?
   → All transports support same HTTPS security model
```

### Transport Recommendations by Use Case

| Use Case | Primary Transport | Secondary Transport | Rationale |
|----------|------------------|-------------------|-----------|
| **Learning A2A** | JSON-RPC 2.0 | None | Standard protocol, best documentation |
| **Production Agent** | JSON-RPC 2.0 | gRPC | Compliance + performance option |
| **High-Performance** | gRPC | JSON-RPC 2.0 | Native streaming + fallback |
| **Simple Integration** | HTTP+JSON | JSON-RPC 2.0 | Familiar REST + A2A standard |
| **Enterprise Multi-Agent** | JSON-RPC 2.0 | gRPC + HTTP+JSON | Maximum compatibility |
| **Real-time Streaming** | gRPC | JSON-RPC 2.0 | Bidirectional streams preferred |

### Performance Characteristics

| Scenario | JSON-RPC 2.0 | gRPC | HTTP+JSON/REST |
|----------|---------------|------|----------------|
| **Single Message** | ~45ms | ~30ms | ~50ms |
| **Streaming (10 msgs)** | ~300ms (SSE) | ~150ms | ~350ms (SSE) |
| **Large Payload (1MB)** | ~180ms | ~100ms | ~200ms |
| **Connection Overhead** | Medium | Low | High |
| **CPU Usage** | Medium | Low | Medium |
| **Memory Usage** | Medium | Low | Medium |
| **Tooling Complexity** | Low | High | Low |

*Note: Performance varies by implementation, network, and payload characteristics.*

## 🧪 Hands-On Transport Exploration

### Test JSON-RPC 2.0 Transport (Standard A2A)

```bash
# Standard A2A JSON-RPC request
curl -X POST https://agent.example.com/a2a/v1 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user", 
        "parts": [
          {
            "kind": "text",
            "text": "What can you help me with?"
          }
        ],
        "messageId": "test-123"
      }
    },
    "id": "req-456"
  }'
```

### Test gRPC Transport (High Performance)

```bash
# Using grpcurl (install: brew install grpcurl)
grpcurl -plaintext \
  -H "authorization: Bearer your-token" \
  -d '{
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "text", 
          "text": "Schedule a meeting"
        }
      ],
      "messageId": "test-456"
    }
  }' \
  agent.example.com:443 a2a.A2AService/SendMessage
```

### Test HTTP+JSON/REST Transport

```bash
# REST-style A2A request
curl -X POST https://agent.example.com/a2a/rest/v1/message:send \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "text",
          "text": "Check my availability for tomorrow"
        }
      ],
      "messageId": "test-789"
    },
    "configuration": {
      "blocking": true
    }
  }'
```

### Test Agent Card Discovery

```bash
# Fetch public Agent Card
curl -X GET https://agent.example.com/.well-known/agent-card.json \
  -H "Accept: application/json"

# Fetch authenticated extended Agent Card (JSON-RPC)
curl -X POST https://agent.example.com/a2a/v1 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{
    "jsonrpc": "2.0",
    "method": "agent/getAuthenticatedExtendedCard",
    "id": "card-req-1"
  }'
```

## 🎓 Transport Theory Takeaways

### Key Concepts to Remember

1. **HTTPS is Mandatory**: All A2A communication MUST use HTTPS in production
2. **Three Equal Transports**: JSON-RPC 2.0, gRPC, and HTTP+JSON are all valid choices
3. **Minimum Compliance**: Agents MUST implement at least one transport
4. **Functional Equivalence**: Multi-transport agents MUST provide identical functionality
5. **Agent Card Declaration**: All supported transports MUST be declared in Agent Card
6. **JSON-RPC 2.0 is Standard**: Most common choice for A2A implementations
7. **Method Mapping Consistency**: Same operations across different transport patterns

### A2A Protocol Compliance

**Agent Requirements**:
- ✅ Implement at least one core transport
- ✅ Support core methods: `message/send`, `tasks/get`, `tasks/cancel`
- ✅ Provide valid Agent Card with transport declarations
- ✅ Use HTTPS with strong TLS in production

**Client Requirements**:
- ✅ Support at least one transport protocol
- ✅ Parse and interpret Agent Card documents
- ✅ Select appropriate transport based on agent capabilities
- ✅ Handle all A2A error codes properly

### Common Misconceptions

❌ **"A2A only uses JSON-RPC"** - A2A supports three equal transport protocols  
❌ **"gRPC is always better"** - Each transport has specific use cases  
❌ **"HTTP+JSON isn't real A2A"** - It's a valid, equal-status transport option  
❌ **"You must choose one transport"** - Agents can support multiple transports  
❌ **"HTTP is allowed"** - HTTPS is mandatory for production A2A systems  
❌ **"Transport negotiation is dynamic"** - Clients select based on static Agent Card  

## 🚀 Next Steps

With A2A transport theory mastered, you're ready to:

1. **Build A2A Fundamentals** (Step 1) - Agent cards, discovery, and compliance
2. **Implement Agent Executors** (Step 2) - Core protocol handling and task management
3. **Add A2A Messaging** (Step 3) - JSON-RPC implementation with proper error handling  
4. **Build Client Messaging** (Step 4) - Multi-agent communication and transport selection
5. **Add Streaming & Tasks** (Step 5) - Real-time streaming and asynchronous task patterns
6. **Implement Client Discovery** (Step 6) - Advanced multi-agent coordination

## ✅ Success Criteria

You've mastered A2A transport theory when you can:

- [ ] **Explain HTTPS requirement**: Understand why A2A mandates HTTPS for production
- [ ] **Compare three transports**: JSON-RPC 2.0, gRPC, and HTTP+JSON strengths/use cases
- [ ] **Understand compliance**: Know minimum requirements for A2A-compliant agents
- [ ] **Map methods across transports**: Understand how operations work across protocols
- [ ] **Design transport strategy**: Choose appropriate combination for your scenario
- [ ] **Read Agent Card declarations**: Interpret transport capabilities from Agent Cards
- [ ] **Plan fallback logic**: Design client transport selection with graceful degradation

---

**Time Investment**: ~1 hour  
**Difficulty**: Beginner to Intermediate  
**Skills Gained**: A2A transport specifications, protocol analysis, implementation planning  
**Prerequisites**: Basic HTTP/HTTPS and JSON knowledge  

**🎉 You now understand A2A transport layer specifications! Ready to implement compliant agent communication?**
