CUS AI Assistant is an AI-powered university chatbot developed as an MCA Major Project. It is designed specifically for Cluster University Srinagar (CUS) to automate student support, simplify access to university information, and provide intelligent conversational assistance.
Unlike traditional chatbots, the system combines:
🤖 Large Language Models (LLMs)
📚 Retrieval-Augmented Generation (RAG)
🧠 Semantic Search
📄 University Knowledge Base
🎯 Intent Detection
🎓 Student Service Automation
The assistant understands natural language queries, retrieves relevant university information, and securely provides personalized student services.
✨ Features
AI Chat Assistant
Natural language conversations
Context-aware responses
Multi-turn conversations
Intent recognition
Hybrid AI + rule-based routing
Retrieval-Augmented Generation (RAG)
Semantic document search
ChromaDB vector database
Document chunking
Embedding generation
Citation-aware responses
Hallucination reduction
Student Services
Students can securely access:
📊 Examination Results
📄 Admit Cards
💰 Fee Receipts
📚 Course Registration
📝 Exam Forms
🎓 Academic Transcripts
📅 Attendance
📦 Migration Certificates
🔁 Revaluation Requests
📑 Xerox Requests
⚠️ Backlog Information
🎫 Helpdesk Services
Admin Panel
Upload university documents
Knowledge base management
Analytics dashboard
Audit logs
Authority management
Document synchronization
AI Features
Intent Detection
Context Management
Semantic Search
Prompt Engineering
Conversation Memory
Streaming Responses
Hybrid Retrieval
🏗 System Architecture
                Student

                   │
                   ▼

        Frontend (HTML/CSS/JS)

                   │

             REST API + SSE

                   │

             FastAPI Backend

                   │

        AI Orchestrator Engine

          ┌────────┴─────────┐
          │                  │
          ▼                  ▼

 Student Services        RAG Pipeline

          │                  │

      SQLite DB        ChromaDB

                             │

                    Ollama Llama 3.2
🛠 Technology Stack
Frontend
HTML5
CSS3
JavaScript
Server Sent Events (SSE)
Backend
FastAPI
Python
SQLAlchemy
Pydantic
Uvicorn
Database
SQLite
ChromaDB (Vector Database)
Artificial Intelligence
Ollama
Llama 3.2
nomic-embed-text
Retrieval-Augmented Generation (RAG)
Semantic Search
Authentication
JWT Authentication
Refresh Tokens
Password Hashing (bcrypt)
🧠 AI Workflow
User Question

      │

      ▼

Intent Detection

      │

      ▼

Is it a Student Service?

 ┌───────────────┐
 │               │
 ▼               ▼

YES             NO

 │               │

 ▼               ▼

Service       RAG Search

 │               │

 ▼               ▼

SQLite      ChromaDB Search

 │               │

 ▼               ▼

Response     Context Retrieved

       │

       ▼

Llama 3.2 Response Generation

       │

       ▼

Streaming Response to User
📂 Project Structure
CUS-AI-BOT/

├── backend/
│   ├── app/
│   ├── models/
│   ├── orchestrator/
│   ├── chat/
│   ├── analytics/
│   ├── authority/
│   ├── ingest/
│   ├── knowledge_sync/
│   ├── database.py
│   └── main.py
│
├── frontend/
│
├── chroma_store/
│
├── documents/
│
├── docker/
│
└── README.md
🔄 Request Flow
Student

   │

   ▼

Frontend

   │

   ▼

FastAPI

   │

   ▼

Intent Router

   │

   ▼

AI Orchestrator

   │

   ▼

Service Connector OR RAG

   │

   ▼

SQLite / ChromaDB

   │

   ▼

LLM

   │

   ▼

Answer
🔒 Security
JWT Authentication
Secure Password Hashing
Refresh Tokens
Session Management
Input Validation
Rate Limiting
Audit Logging
Role-Based Access
📊 Database
The project uses:
Relational Database
SQLite stores:
Users
Students
Conversations
Messages
Results
Attendance
Fee Receipts
Transcripts
Audit Logs
Helpdesk
Analytics
Vector Database
ChromaDB stores:
University documents
Chunk embeddings
Semantic vectors
Knowledge base
🚀 Installation
Clone the repository
git clone https://github.com/Javaid901/CUS-AI-BOT.git
Go to project
cd CUS-AI-BOT
Install dependencies
pip install -r requirements.txt
Run Ollama
ollama serve
Pull required models
ollama pull llama3.2:1b
ollama pull nomic-embed-text
Start backend
python -m uvicorn app.main:app --reload
📌 Future Enhancements
PostgreSQL support
Mobile application
Voice Assistant
Kashmiri Language Support
Urdu Language Support
ERP Integration
OCR-based Document Processing
Multi-University Deployment
Advanced Analytics Dashboard
🎯 Learning Outcomes
This project demonstrates practical implementation of:
Artificial Intelligence
Large Language Models
Retrieval-Augmented Generation (RAG)
FastAPI Backend Development
Semantic Search
Vector Databases
JWT Authentication
REST APIs

Real-time Streaming
Database Design
System Architecture
