---
title: "02: Authorization Server Metadata"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# 02: Authorization Server Metadata

**Objective:** Create a simple OAuth Authorization Server and demonstrate how clients discover its capabilities through the `/.well-known/oauth-authorization-server` endpoint.

Building on step 01 where we discovered the Authorization Server's URL (`http://localhost:9000`), this step implements the second half of the discovery process: querying the Authorization Server itself to learn about its specific endpoints and capabilities.

## Dual Implementation Approach

This step provides **TWO complete implementations**:

### 📚 **Custom Implementation** (Educational)
- **Purpose**: Learn OAuth fundamentals by building a simple server from scratch
- **Technology**: Pure Python HTTP server (~200 lines)
- **Benefits**: Understand OAuth concepts without external dependencies
- **Use Case**: Learning, development, understanding the specifications

### 🏭 **Keycloak Implementation** (Production)  
- **Purpose**: Use enterprise-grade OAuth server for real-world scenarios
- **Technology**: Keycloak in Docker with full OIDC support
- **Benefits**: Production-ready features, security, scalability
- **Use Case**: Production deployment, enterprise environments

---

## What You Will Learn in This Step

This step focuses on **Authorization Server Metadata Discovery** as defined in [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414).

By the end of this lesson, you will understand and have implemented:
1. A simple OAuth Authorization Server that serves metadata at `/.well-known/oauth-authorization-server`
2. A client that queries this endpoint to discover the server's capabilities
3. The two-stage discovery process required by the MCP specification
4. The difference between educational and production OAuth implementations

## Key OAuth Concepts

-   **Authorization Server Metadata (RFC 8414):** A standardized JSON document that tells clients about the Authorization Server's endpoints and capabilities
-   **`/.well-known/oauth-authorization-server`:** The standard endpoint where this metadata is served
-   **Endpoint Discovery:** How clients learn the specific URLs for:
    -   `authorization_endpoint`: Where to send users for login and consent
    -   `token_endpoint`: Where to exchange authorization codes for access tokens
    -   `registration_endpoint`: Where clients can register themselves dynamically
    -   `jwks_uri`: Where to find public keys for token verification

## Two-Server Discovery Flow

This step completes the two-stage discovery process:

1. **Step 01 (✅ Completed):** Client queries MCP server at `/.well-known/oauth-protected-resource` and learns the Authorization Server is at `http://localhost:9000`
2. **Step 02 (This step):** Client queries Authorization Server at `/.well-known/oauth-authorization-server` and learns the specific endpoints for registration, authorization, and token exchange

### Run code

```bash
uv run uvicorn server:mcp_app --reload
```

```bash
uv run uvicorn authorization_server:app --port 9000 --reload
```

```bash
uv run client.py
```

### Run Keycloak implementation

```bash
# Terminal 1: Start Keycloak
cd open_source/keycloak
docker-compose up -d

# Terminal 2: Start MCP server (from step 01)
uv run uvicorn server:mcp_app --reload

# Terminal 3: Run discovery client
uv run client.py

# Optional: Check Keycloak Logs
docker-compose logs -f keycloak
```

## Implementation Approach

This step demonstrates the **end-to-end OAuth discovery flow** by building on the step 01 MCP server code. Instead of separate servers, we create an integrated solution that shows both stages working together.

### Custom Implementation (Educational)

The custom implementation provides clean separation of concerns with three components:

-   **`server.py`:** 
    -   **MCP Resource Server** on `http://localhost:8000` (same as step 01)
    -   Provides Protected Resource metadata endpoint
    -   Returns 401 for unauthenticated requests to trigger discovery

-   **`authorization_server.py`:** 
    -   **OAuth Authorization Server** on `http://localhost:9000` 
    -   Provides Authorization Server metadata endpoint
    -   Mock OAuth endpoints for future lessons

-   **`client.py`:** Discovery client that performs the complete flow:
    1.  **Step 01:** Makes unauthenticated request to MCP server → gets 401 → fetches Protected Resource metadata → finds Authorization Server URL
    2.  **Step 02:** Queries Authorization Server metadata → discovers specific OAuth endpoints
    3.  **Summary:** Shows complete endpoint discovery results
---

## 📚 Custom Implementation

### Files Structure
```
custom/
├── server.py                 # MCP Resource Server (from step 01)
├── authorization_server.py   # OAuth Authorization Server
├── client.py                 # Discovery client (two-stage flow)
└── pyproject.toml            # Dependencies (MCP, FastAPI, uvicorn)
```

### Running the Custom Demo

You need to run 3 components in separate terminals:

#### Terminal 1: Start MCP Resource Server
```bash
cd custom
uv run uvicorn server:mcp_app --host localhost --port 8000 --reload
```

#### Terminal 2: Start Authorization Server  
```bash
cd custom
uv run uvicorn authorization_server:app --host localhost --port 9000 --reload
```

#### Terminal 3: Run Discovery Client
```bash
cd custom
uv run client.py
```

### Custom Implementation Features
- ✅ **Clean separation**: MCP server and Authorization Server as separate components
- ✅ **End-to-end demo**: Shows complete two-stage discovery flow
- ✅ RFC 8414 compliant Authorization Server metadata endpoint  
- ✅ RFC 9728 compliant Protected Resource metadata (from step 01)
- ✅ Mock OAuth endpoints for future lessons (authorization, token, registration)
- ✅ Educational logging and clear error messages
- ✅ Simple uvicorn commands for each component

---

## 🏭 Keycloak Implementation

### Files Structure
```
open_source/keycloak/
├── docker-compose.yml         # Keycloak setup
├── realm-export.json         # Pre-configured realm
└── client.py                 # Production client
```

### Running the Keycloak Demo

1. **Start the MCP Server** (from step 01):
   ```bash
   cd ../01_protected_resource_metadata/mcp_code
   uv run uvicorn server:mcp_app --reload
   ```

2. **Start Keycloak**:
   ```bash
   cd open_source/keycloak
   docker-compose up -d
   
   # Monitor startup (takes 30-60 seconds)
   docker-compose logs -f keycloak
   ```

3. **Run the Keycloak Discovery Client**:
   ```bash
   python client.py
   ```

4. **Access Keycloak Admin Console** (optional):
   - URL: http://localhost:9000/admin
   - Username: `admin`
   - Password: `admin123`

### Keycloak Implementation Features
- ✅ Production-ready OAuth 2.1 + OIDC server
- ✅ Real user authentication (mcpuser/password123)
- ✅ Proper JWT signing and validation
- ✅ Dynamic client registration
- ✅ PKCE support for public clients
- ✅ Refresh tokens and session management
- ✅ Admin console for configuration
- ✅ Built-in security features (brute force protection, etc.)
- ✅ Scalable for production deployment

---

## Comparison: Custom vs Keycloak

| Feature | Custom Implementation | Keycloak Implementation |
|---------|----------------------|------------------------|
| **Learning Value** | ⭐⭐⭐⭐⭐ High - See every detail | ⭐⭐⭐ Medium - Focus on integration |
| **Production Ready** | ❌ Demo only | ✅ Enterprise grade |
| **Setup Complexity** | ⭐ Simple - Run Python file | ⭐⭐⭐ Medium - Docker required |
| **Security Features** | ⭐ Basic demo security | ⭐⭐⭐⭐⭐ Full security suite |
| **Customization** | ⭐⭐⭐⭐⭐ Full control | ⭐⭐⭐ Configuration based |
| **Dependencies** | None | Docker + Keycloak |
| **Performance** | ⭐⭐ Adequate for learning | ⭐⭐⭐⭐⭐ High performance |
| **Standards Compliance** | ⭐⭐⭐ Basic compliance | ⭐⭐⭐⭐⭐ Full RFC compliance |

## Expected Output

Both implementations will demonstrate:

1. **Stage 1**: Discovery of Authorization Server URL from MCP server
2. **Stage 2**: Discovery of specific endpoints from Authorization Server
3. **Results**: Complete endpoint information for next steps

### Custom Implementation Output
```
🚀 OAuth 2.0 Two-Stage Discovery Client
============================================================
🔍 Stage 1: Discovering Authorization Server URL from MCP server
✅ Received expected 401 Unauthorized response
✅ Successfully retrieved MCP server metadata
🎯 Found Authorization Server URL: http://localhost:9000

🔍 Stage 2: Discovering Authorization Server metadata
✅ Successfully retrieved Authorization Server metadata

🎉 DISCOVERY COMPLETE - Two-Stage OAuth Discovery Results
============================================================
📋 STAGE 1: MCP Server Protected Resource Metadata
🔐 Authorization Endpoint: http://localhost:9000/authorize
🎫 Token Endpoint: http://localhost:9000/token
📝 Registration Endpoint: http://localhost:9000/register
```

### Keycloak Implementation Output
```
🚀 OAuth 2.0 Discovery with Keycloak (Production)
================================================================================
✅ Keycloak health check passed: UP
🔍 Stage 1: Discovering MCP Protected Resource Metadata
✅ Successfully retrieved MCP server metadata

🔍 Stage 2: Discovering Keycloak Authorization Server Metadata
✅ Successfully retrieved Keycloak metadata

🎉 KEYCLOAK OAUTH DISCOVERY COMPLETE
================================================================================
🏢 Issuer: http://localhost:9000/realms/mcp-oauth
🔐 Authorization Endpoint: http://localhost:9000/realms/mcp-oauth/protocol/openid-connect/auth
🎫 Token Endpoint: http://localhost:9000/realms/mcp-oauth/protocol/openid-connect/token
🔑 JWKS URI: http://localhost:9000/realms/mcp-oauth/protocol/openid-connect/certs
👤 UserInfo Endpoint: http://localhost:9000/realms/mcp-oauth/protocol/openid-connect/userinfo

🚀 PRODUCTION FEATURES AVAILABLE:
   ✅ Real user authentication (username: mcpuser, password: password123)
   ✅ Proper JWT token signing and validation
   ✅ PKCE support for public clients
   ✅ Admin console at http://localhost:9000/admin
```

## Next Steps

With both discovery stages complete, the next lessons will use these discovered endpoints to:

### For Custom Implementation
1. **Register the client** with the simple Authorization Server using the `registration_endpoint`
2. **Implement the authorization flow** using mock user consent
3. **Validate tokens** using basic JWT verification

### For Keycloak Implementation  
1. **Register the client** using Keycloak's dynamic registration or admin console
2. **Implement real user login** with actual authentication
3. **Validate tokens** using Keycloak's JWKS endpoint
4. **Deploy to production** with proper HTTPS and database persistence

## DACA Integration Notes

Both implementations support the **Dapr Agentic Cloud Ascent (DACA)** architecture:

- **Custom**: Perfect for local development and learning (DACA stage 1)
- **Keycloak**: Production-ready for enterprise and planetary scale (DACA stages 3-4)
- **Agent-to-Agent**: Both support client credentials flow needed for 10M concurrent agents
- **Kubernetes Ready**: Keycloak can be deployed on Kubernetes with Dapr integration
