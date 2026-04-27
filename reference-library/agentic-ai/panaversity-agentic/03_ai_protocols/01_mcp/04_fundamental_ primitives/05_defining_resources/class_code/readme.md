---
title: "MCP ClientSession Learning Project"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# MCP ClientSession Learning Project

This repository contains code examples and learning materials from our **Class-05: Model Context Protocol - Defining Resources and MCP Client** session.

## 📺 Class Recording

**YouTube Live Session:** [Class-05: Model Context Protocol - Defining Resources and MCP Client](https://www.youtube.com/live/k12RclRbzUA?si=PNiBjI7KdHwTEwC-)

## 🎯 Learning Objectives

This project demonstrates the learning approach for understanding Model Context Protocol (MCP) server and client implementation with resources:

1. **MCP Server Implementation** - Creating FastMCP server with resources
2. **Resource Definition** - Understanding URI schemes and resource patterns
3. **Resource Templates** - Dynamic resource URI templates
4. **Enhanced MCP Client** - Resource listing, reading, and template handling

## 📁 Project Structure

```
mcp_client/
├── client.py            # Enhanced MCP client implementation
├── server.py            # FastMCP server with resources
├── rough_work.txt       # URI schemes and resource patterns
├── pyproject.toml       # Project dependencies
└── README.md            # This file
```

## 🚀 Learning Progression

### 1. MCP Server Implementation (`server.py`)

**Topics Covered:**
- FastMCP server creation
- Resource definition with URI schemes
- Resource templates with dynamic parameters
- Stateless HTTP server configuration

**Key Concepts:**
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="FastMCP", stateless_http=True)

@mcp.resource("docs://documents", mime_type="application/json")
def list_docs():
    """List all available documents."""
    return list(docs.keys())

@mcp.resource("docs://documents/{doc_name}", mime_type="application/json")
def read_doc(doc_name: str):
    """Read a specific document."""
    if doc_name in docs:
        return {"name": doc_name, "content": docs[doc_name]}
    else:
        raise mcp.ResourceNotFound(f"Document '{doc_name}' not found.")
```

### 2. Enhanced MCP Client (`client.py`)

**Topics Covered:**
- Resource listing and reading
- Resource template handling
- JSON resource processing
- Enhanced error handling

**Key Concepts:**
```python
async def list_resouces(self) -> list[types.Resource]:
    assert self._sess, "Session not available."
    result:types.ListResourcesResult = await self._sess.list_resources()
    return result.resources

async def read_resources(self, uri: str) -> types.ReadResourceResult:
    assert self._sess, "Session not available."
    result = await self._sess.read_resource(AnyUrl(uri))
    resource = result.contents[0]
    if isinstance(resource, types.TextResourceContents):
        if resource.mimeType == "application/json":
            try:
                return json.loads(resource.text)
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON: {e}")
    return resource.text
```

### 3. URI Schemes and Resource Patterns (`rough_work.txt`)

**Topics Covered:**
- Understanding URI schemes (docs://, binary://, db://, etc.)
- Resource pattern design
- Different resource types and their use cases

**Key URI Schemes:**
- `docs://documents` - Document management
- `binary://logo` - Binary files (audio, PDF, images)
- `db://pana` - Database resources
- `file://path` - File system resources
- `s3://bucket` - Cloud storage resources


## 🛠️ Setup and Installation

### Prerequisites
- Python 3.13 or higher
- An MCP server running on `http://localhost:8000/mcp`

### Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd /learn-agentic-ai/03_ai_protocols/01_mcp/04_fundamental_ 20primitives/05_defining_resources/class_code
   ```

2. **Install dependencies:**
   ```bash
   uv sync
   ```

    **OR**

   ```bash
   pip install -e .
   ```

3. **Run examples:**

   ```bash
   # Start MCP Server (in one terminal)
   uv run uvicorn server:mcp_server --reload
   
   # Run Enhanced MCP Client (in another terminal)
   uv run client.py
   ```

## 📚 Dependencies

- `mcp>=1.12.1` - Model Context Protocol library
- `pydantic>=2.11.7` - Data validation and settings management
- `requests>=2.32.4` - HTTP library for streaming

## 🎓 Learning Outcomes

After completing this project, students will understand:

1. **MCP Server Development**
   - FastMCP server creation and configuration
   - Resource definition and URI scheme design
   - Resource templates with dynamic parameters
   - Error handling and resource validation

2. **Resource Management**
   - Understanding different URI schemes (docs://, binary://, db://, etc.)
   - Resource listing and reading operations
   - JSON resource processing and validation
   - Resource template handling and dynamic URI generation

3. **Enhanced Client-Server Communication**
   - Complete MCP client-server interaction
   - Resource discovery and access patterns
   - Proper error handling and validation

## 🔗 Additional Resources

- [Model Context Protocol Documentation](https://modelcontextprotocol.io/)
- [MCP Python Client Library](https://github.com/modelcontextprotocol/python-sdk)
- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)

## 📝 Notes

This project is designed for educational purposes and demonstrates the progressive learning approach from basic Python concepts to full MCP client implementation. Each file builds upon the previous concepts, making it easier for students to understand the complete picture.

---

**Class:** Model Context Protocol - Defining Resources and MCP Client  
**Date:** 30 July 2025  
**YouTube:** [https://www.youtube.com/live/k12RclRbzUA?si=PNiBjI7KdHwTEwC-](https://www.youtube.com/live/k12RclRbzUA?si=PNiBjI7KdHwTEwC-)
