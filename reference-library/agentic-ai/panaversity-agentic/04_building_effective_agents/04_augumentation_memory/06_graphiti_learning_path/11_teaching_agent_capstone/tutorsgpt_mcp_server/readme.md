---
title: "TutorsGPT Memory MCP Server"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# TutorsGPT Memory MCP Server

A comprehensive educational memory system built on Graphiti temporal knowledge graphs, leveraging everything learned from Steps 01-08 of the Graphiti learning path.

## 🎯 **What This Is**

The **TutorsGPT Memory MCP Server** is the culmination of the Graphiti learning journey. It combines:

- ✅ **Episodes** (Step 02) for natural learning conversations
- ✅ **Custom Types** (Step 03) for educational domain modeling  
- ✅ **Communities** (Step 04) for knowledge clustering
- ✅ **Namespacing** (Step 05) for multi-user isolation
- ✅ **Search** (Step 06) for intelligent retrieval
- ✅ **CRUD** (Step 07) for precise data management
- ✅ **Fact Triples** (Step 08) for structured relationships

Into a **production-ready educational AI memory system**.

## 🚀 **Key Features**

### **1. Student Profile Management**
- Create comprehensive student profiles with learning styles and preferences
- Track learning goals, subjects, and special accommodations
- Isolated memory spaces per student using namespacing

### **2. Learning Context Engineering**
- Generate rich context for AI tutoring sessions
- Analyze learning history, struggles, and successes
- Provide personalized tutoring recommendations

### **3. Progress Tracking & Analytics**
- Track concept mastery using structured fact triples
- Generate learning insights and progress analytics
- Compare learning patterns across time periods

### **4. Intelligent Recommendations**
- Suggest next learning topics based on progress
- Identify knowledge gaps preventing concept mastery
- Assess learning readiness for new concepts

### **5. Multi-User Architecture**
- Complete isolation between students using group namespacing
- Scalable to thousands of concurrent learners
- Privacy-preserving educational memory

## 📁 **Project Structure**

```
tutorsgpt_mcp_server/
├── main.py                 # Main MCP server entry point
├── educational_types.py    # Educational domain models (Step 03)
├── student_tools.py        # Student management tools
├── learning_tools.py       # Learning session & mastery tools
├── context_tools.py        # Context engineering tools
├── analytics_tools.py      # Learning analytics tools
├── demo_client.py          # Demo client showcasing capabilities
├── env_example.txt         # Environment configuration template
└── README.md              # This file
```

## 🛠️ **Setup & Installation**

### **Prerequisites**

1. **Neo4j Database** (version 5.26+)
2. **Google Gemini API Key** for LLM operations
3. **Python 3.10+** with uv package manager

### **Installation Steps**

1. **Clone and Navigate**
   ```bash
   cd tutorsgpt_mcp_server
   ```

2. **Install Dependencies**
   ```bash
   uv add graphiti-core python-dotenv fastmcp pydantic
   ```

3. **Configure Environment**
   ```bash
   cp env_example.txt .env
   # Edit .env with your actual credentials
   ```

4. **Start Neo4j Database**
   - Local: Start your Neo4j instance
   - Cloud: Use Neo4j AuraDB free tier

5. **Run the MCP Server**
   ```bash
   uv run python main.py
   ```

## 🎓 **Educational Tools Available**

### **Student Management**
- `create_student_profile()` - Create comprehensive student profiles
- `get_student_profile()` - Retrieve student information and history
- `search_student_memory()` - Search across all student learning data

### **Learning Sessions**
- `start_learning_session()` - Begin tracked learning sessions
- `end_learning_session()` - Complete sessions with outcomes
- `track_concept_mastery()` - Record mastery using fact triples
- `recommend_next_topics()` - Suggest personalized learning paths

### **Context Engineering**
- `get_tutoring_context()` - Rich context for AI tutoring
- `analyze_learning_gaps()` - Identify knowledge prerequisites
- `get_learning_readiness()` - Assess readiness for concepts

### **Learning Analytics**
- `get_learning_insights()` - Comprehensive learning analytics
- `get_progress_summary()` - Progress summaries by subject
- `compare_learning_patterns()` - Compare learning across time periods

## 🧪 **Demo & Testing**

### **Run the Demo**

```bash
# Start the MCP server (in one terminal)
uv run python main.py

# Run the demo client (in another terminal)
uv run python demo_client.py
```

The demo showcases:
1. **Student Profile Creation** with custom educational types
2. **Learning Session Management** with episodes and CRUD
3. **Concept Mastery Tracking** using fact triples
4. **Context Engineering** for personalized tutoring
5. **Learning Gap Analysis** combining search techniques
6. **Learning Analytics** with comprehensive insights
7. **Topic Recommendations** based on progress patterns
8. **Memory Search** across all educational data

## 🏗️ **Architecture Highlights**

### **Multi-User Namespacing (Step 05)**
```python
# Each student gets isolated memory space
namespace = f"student_{student_id}_{course}_{semester}"
# Example: "student_alice_123_cs101_fall2024"
```

### **Educational Domain Types (Step 03)**
```python
class Student(BaseModel):
    student_id: str
    learning_style: str  # Visual, Auditory, Kinesthetic
    current_level: str   # Beginner, Intermediate, Advanced
    # ... rich educational metadata

class TutoringContext(BaseModel):
    learning_history: List[Dict]
    recent_struggles: List[str]
    recommended_approach: str
    # ... context for AI tutoring
```

### **Fact Triple Integration (Step 08)**
```python
# Structured educational relationships
(Alice, MASTERED, Variables) → (Variables, PREREQUISITE_FOR, Loops)
(Alice, COMPLETED, Python_Quiz) → (Python_Quiz, ASSESSES, Programming_Fundamentals)
```

### **Intelligent Search (Step 06)**
```python
# Context-aware educational queries
await graphiti.search(
    query="student alice learning experience python loops",
    group_id="student_alice_123_cs101_fall2024",
    limit=10
)
```

## 📊 **Real-World Usage Scenarios**

### **Scenario 1: AI Tutor Integration**
```python
# AI tutor gets rich context about student
context = await get_tutoring_context("alice_123", "python_loops")

# Context includes:
# - Alice prefers visual explanations
# - She mastered variables but struggled with conditionals  
# - Recommended approach: "Use step-by-step visual examples"
```

### **Scenario 2: Learning Progress Dashboard**
```python
# Generate comprehensive learning insights
insights = await get_learning_insights("alice_123", "last_30_days")

# Returns:
# - Concepts mastered: 5
# - Learning velocity: 2.1 concepts/week
# - Strength areas: ["Visual learning", "Hands-on practice"]
# - Recommendations: ["Continue visual approach", "Add abstract practice"]
```

### **Scenario 3: Adaptive Learning System**
```python
# Check if student is ready for advanced topics
readiness = await get_learning_readiness("alice_123", "Functions,Classes,Inheritance")

# Returns readiness scores and prerequisite analysis
# Enables adaptive curriculum sequencing
```

## 🔧 **Technical Implementation**

### **Leverages All Graphiti Steps:**

1. **Episodes (Step 02)**: Natural language learning conversations and session notes
2. **Custom Types (Step 03)**: Rich educational domain modeling with Pydantic
3. **Communities (Step 04)**: Knowledge clustering for related concepts  
4. **Namespacing (Step 05)**: Multi-user isolation and privacy
5. **Search (Step 06)**: Intelligent retrieval across learning history
6. **CRUD (Step 07)**: Precise updates to student progress and profiles
7. **Fact Triples (Step 08)**: Structured educational relationships and prerequisites

### **Educational Memory Architecture:**

```
Student Memory Space (Isolated by Namespace)
├── Profile Data (Custom Types)
├── Learning Episodes (Natural Language)
├── Mastery Facts (Structured Triples)
├── Session History (CRUD Managed)
├── Progress Analytics (Computed Insights)
└── Context Cache (Search Results)
```

## 🎯 **Benefits for Educational AI**

1. **Personalized Learning**: Each student gets AI tutoring adapted to their unique journey
2. **Context-Aware AI**: Tutors understand student history, struggles, and preferences
3. **Learning Analytics**: Track patterns, identify gaps, measure progress over time
4. **Scalable Architecture**: Multi-user system with complete privacy isolation
5. **Rich Memory**: Combines structured data with natural language understanding

## 🚀 **Next Steps**

This TutorsGPT Memory MCP Server represents the practical culmination of the Graphiti learning path. It can be:

1. **Integrated with AI Tutoring Systems**: Provide memory to OpenAI Agents, Claude, or custom AI tutors
2. **Connected to Learning Management Systems**: Sync with Canvas, Moodle, or custom LMS platforms
3. **Enhanced with Real-Time Analytics**: Add dashboards for educators and administrators
4. **Scaled to Production**: Deploy on Kubernetes with proper monitoring and security

## 📚 **Learning Journey Complete**

By building this system, you've mastered:
- ✅ Temporal knowledge graph concepts and implementation
- ✅ Educational domain modeling with custom types
- ✅ Multi-user system architecture with proper isolation
- ✅ Intelligent search and retrieval across complex data
- ✅ Structured relationship management with fact triples
- ✅ Production-ready MCP server development
- ✅ Educational AI memory system design

**Congratulations!** You now have the knowledge and tools to build sophisticated educational AI systems with advanced memory capabilities.

---

**Key Takeaway**: This TutorsGPT Memory MCP Server transforms theoretical knowledge from Steps 01-08 into a practical, production-ready system that can power the next generation of educational AI applications. 🎓

*"From learning the fundamentals to building the future of educational AI - your Graphiti journey is complete!"*
