"""
Local AI Chatbot Deployment Console - Main Application

A multi-chatbot AI control platform for creating, managing, and deploying
chatbots with various LLM providers.

Author: AI Console Team
Version: 1.0.0
"""

import os
import sys
import logging
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from .services.logging_utils import JsonFormatter, redact_sensitive_text

# Setup logging before anything else
def setup_logging():
    """Configure logging to file and console."""

    # Create logs directory
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # Log filename with timestamp
    log_file = os.path.join(log_dir, f"app_{datetime.now().strftime('%Y%m%d')}.log")
    
    # Create formatter
    formatter = JsonFormatter()
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    
    return log_file

# Initialize logging
log_file_path = setup_logging()
logger = logging.getLogger(__name__)
logger.info(f"=== Application Starting ===")
logger.info(f"Log file: {log_file_path}")
logger.info(f"Python version: {sys.version}")
logger.info(f"Working directory: {os.getcwd()}")

# Load environment variables
logger.info("Loading environment variables...")
load_dotenv()
logger.info("Environment variables loaded")

# Import routers
logger.info("Importing routers...")
try:
    from .routers import bots, chat, admin, scraper, templates, embed
    logger.info("Routers imported successfully")
except Exception as e:
    logger.exception(f"Failed to import routers: {e}")
    raise

# Initialize database
logger.info("Initializing database...")
try:
    from .models.database import Bot, SessionLocal, init_db, init_db_async
    logger.info("Database module imported")
except Exception as e:
    logger.exception(f"Failed to import database module: {e}")
    raise

# Initialize templates
logger.info("Importing template service...")
try:
    from .services.template_service import template_service
    logger.info("Template service imported")
except Exception as e:
    logger.exception(f"Failed to import template service: {e}")
    raise

logger.info("Importing tunnel service...")
try:
    from .services.tunnel_service import tunnel_service
    logger.info("Tunnel service imported")
except Exception as e:
    logger.exception(f"Failed to import tunnel service: {e}")
    raise

logger.info("Importing API gateway service...")
try:
    from .services.api_gateway_service import api_gateway_service
    from .services.health_service import health_service
    from .services.metrics_service import metrics_service
    from .services.safe_mode_service import safe_mode_service
    from .services.startup_warmup_service import startup_warmup_service
    from .services.system_inspection_service import (
        build_supervisor_health_report,
        refresh_observability_metrics,
    )
    from .services.supervisor_service import supervisor_service
    from .services.standalone_model_service import standalone_model_service
    logger.info("API gateway service imported")
except Exception as e:
    logger.exception(f"Failed to import API gateway service: {e}")
    raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Local AI Chatbot Deployment Console...")
    print("Starting Local AI Chatbot Deployment Console...")
    runtime_role = (os.getenv("APP_RUNTIME_ROLE", "api").strip().lower() or "api")
    api_lease_owner = f"api-{os.getpid()}"
    
    try:
        await init_db_async()
        logger.info("Database initialized")
        print("Database initialized")
        health_service.set_component("database", "healthy", critical=True, detail="database initialized")
    except Exception as e:
        logger.exception(f"Failed to initialize database: {e}")
        health_service.set_component("database", "unhealthy", critical=True, detail=str(e))
        raise
    
    try:
        template_service._ensure_default_templates()
        logger.info("Templates loaded")
        print("Templates loaded")
        health_service.set_component("templates", "healthy", critical=False, detail="templates loaded")
    except Exception as e:
        logger.exception(f"Failed to load templates: {e}")
        health_service.set_component("templates", "unhealthy", critical=False, detail=str(e))
        raise

    registry_db = SessionLocal()
    try:
        imported_count = standalone_model_service.sync_db_from_folders(registry_db)
        standalone_model_service.ensure_folders_for_existing_bots(registry_db.query(Bot).all())
        health_service.set_component(
            "storage_registry",
            "healthy",
            critical=False,
            detail="portable model registry synchronized",
            metadata={"imported_bots": imported_count},
        )
        logger.info("Portable model registry synchronized (%s imported bot folders)", imported_count)
    except Exception as e:
        logger.exception("Failed to synchronize standalone model folders: %s", e)
        health_service.set_component("storage_registry", "degraded", critical=False, detail=str(e))
        raise
    finally:
        registry_db.close()

    logger.info("Admin Dashboard: http://localhost:8000/admin")
    logger.info("API Documentation: http://localhost:8000/docs")
    print("Admin Dashboard: http://localhost:8000/admin")
    print("API Documentation: http://localhost:8000/docs")
    logger.info(
        "Running API role '%s'. Gateway/tunnel/scraper/warmup now require separate supervisor roles.",
        runtime_role,
    )

    async def _api_lease_loop() -> None:
        while True:
            lease_db = SessionLocal()
            try:
                supervisor_service.upsert_component_lease(
                    lease_db,
                    component_name="api",
                    unit_type="api",
                    lease_owner=api_lease_owner,
                    state="running",
                    metadata={
                        "pid": os.getpid(),
                        "role": runtime_role,
                        "origin_url": os.getenv("APP_ORIGIN_URL", "http://127.0.0.1:8000"),
                    },
                )
            finally:
                lease_db.close()
            health_service.set_component(
                "api",
                "healthy",
                critical=True,
                detail="api serving",
                metadata={"pid": os.getpid(), "role": runtime_role},
            )
            await asyncio.sleep(5)

    api_lease_task = asyncio.create_task(_api_lease_loop())
    warmup_enabled = runtime_role == "api" and os.getenv("API_STARTUP_WARMUP_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    if warmup_enabled:
        await startup_warmup_service.start()
    
    yield
    
    # Shutdown
    api_lease_task.cancel()
    try:
        await api_lease_task
    except asyncio.CancelledError:
        pass
    lease_db = SessionLocal()
    try:
        supervisor_service.upsert_component_lease(
            lease_db,
            component_name="api",
            unit_type="api",
            lease_owner=api_lease_owner,
            state="stopped",
            metadata={"pid": os.getpid(), "role": runtime_role},
        )
    finally:
        lease_db.close()
    if warmup_enabled:
        await startup_warmup_service.shutdown()

    logger.info("Shutting down...")
    print("Shutting down...")

# Create FastAPI application
app = FastAPI(
    title="Local AI Chatbot Deployment Console",
    description="""
    A multi-chatbot AI control platform that allows you to create, clone, configure, 
    scrape, manage, deploy, enable, disable, and expose chatbots over API endpoints 
    or embeddable UI links.
    
    ## Features
    
    * **Multi-Bot Management** - Create and manage multiple chatbots
    * **Template System** - Pre-configured templates for different providers
    * **Knowledge Base** - Upload documents and websites to create RAG-enabled bots
    * **Website Scraper** - Automatically extract content from websites
    * **Analytics** - Track usage, tokens, and performance
    * **Embeddable Widget** - Easy-to-embed chat widgets for websites
    
    ## Providers
    
    * OpenAI (GPT-4.1, GPT-4o, etc.)
    * Local LLM (llama.cpp, Ollama, LM Studio)
    * Anthropic Claude (Future)
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(bots.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(scraper.router)
app.include_router(templates.router)
app.include_router(embed.router)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    is_public_bot_route = request.url.path.startswith("/api/bot/")
    errors = exc.errors()
    if is_public_bot_route and any(error.get("type") == "json_invalid" for error in errors):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Malformed JSON request body"},
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, str):
        safe_detail = redact_sensitive_text(detail)
    else:
        safe_detail = detail
    headers = getattr(exc, "headers", None)
    return JSONResponse(status_code=exc.status_code, content={"detail": safe_detail}, headers=headers)

# Static files (for admin UI)
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# React frontend static files
frontend_dist_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
frontend_assets_path = os.path.join(frontend_dist_path, "assets")
frontend_index_path = os.path.join(frontend_dist_path, "index.html")

# Check if React frontend build exists
REACT_FRONTEND_AVAILABLE = os.path.exists(frontend_index_path)
if REACT_FRONTEND_AVAILABLE:
    logger.info("React frontend found, serving from frontend/dist")
    app.mount("/assets", StaticFiles(directory=frontend_assets_path), name="frontend-assets")
    # Serve favicon if exists
    if os.path.exists(os.path.join(frontend_dist_path, "favicon.svg")):
        app.mount("/favicon.svg", StaticFiles(directory=frontend_dist_path), name="favicon")
else:
    logger.info("React frontend not found, using fallback UI")


@app.get("/", response_class=RedirectResponse)
async def root():
    """Redirect to admin dashboard."""
    return RedirectResponse(url="/admin")


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/{path:path}", response_class=HTMLResponse)
async def admin_dashboard(request: Request, path: str = ""):
    """Serve the admin dashboard."""
    # Serve React frontend if available
    if REACT_FRONTEND_AVAILABLE:
        with open(frontend_index_path, "r", encoding="utf-8") as f:
            return f.read()
    # Fallback to inline HTML
    return get_admin_html()


@app.get("/login", response_class=RedirectResponse)
async def legacy_login_redirect():
    """Keep stale frontend redirects and bookmarks from breaking the admin UI."""
    return RedirectResponse(url="/admin/login")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_redirect():
    """Serve the frontend favicon when the browser asks for /favicon.ico."""
    favicon_path = os.path.join(frontend_dist_path, "favicon.svg")
    if os.path.exists(favicon_path):
        return RedirectResponse(url="/favicon.svg")
    return PlainTextResponse("", status_code=204)


@app.get("/health/live")
async def health_live():
    """Liveness probe for process supervision."""
    return {
        "status": "alive",
        "role": "api",
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/health/ready")
async def health_ready():
    """Readiness probe backed by DB connectivity and supervisor leases."""
    report = build_supervisor_health_report()
    critical_components = ["database", "api"]
    critical_ok = all(
        report["components"].get(component, {}).get("status") == "healthy"
        for component in critical_components
    )
    return {
        "status": "ready" if critical_ok else "not_ready",
        "hot_state": report.get("hot_state", "cold"),
        "hot_ready": bool(report.get("hot_ready")),
        "warmup": report.get("warmup", {}),
        "critical_components": {name: report["components"].get(name) for name in critical_components},
        "safe_modes": report.get("safe_modes", []),
        "generated_at": report["generated_at"],
    }


@app.get("/health/fresh")
async def health_fresh():
    """Freshness probe for stale leases and degraded dependency state."""
    report = build_supervisor_health_report()
    degraded_components = [
        name for name, payload in report["components"].items()
        if payload.get("status") != "healthy"
    ]
    is_fresh = not degraded_components and not report.get("safe_modes")
    return {
        "status": "fresh" if is_fresh else "stale",
        "hot_state": report.get("hot_state", "cold"),
        "hot_ready": bool(report.get("hot_ready")),
        "degraded_components": degraded_components,
        "safe_modes": report.get("safe_modes", []),
        "generated_at": report["generated_at"],
    }


@app.get("/health")
async def health_check():
    """Supervisor-backed health report."""
    report = build_supervisor_health_report()
    tunnel_metadata = report["components"].get("tunnel-agent", {}).get("metadata", {})
    return {
        "status": report["status"],
        "hot_state": report.get("hot_state", "cold"),
        "hot_ready": bool(report.get("hot_ready")),
        "warmup": report.get("warmup", {}),
        "version": "1.0.0",
        "public_base_url": report.get("public_base_url"),
        "cloudflare_tunnel": {
            "running": report["components"].get("tunnel-agent", {}).get("status") == "healthy",
            "source": tunnel_metadata.get("source"),
        },
        "api_gateway": report.get("api_gateway", {}),
        "components": report["components"],
        "safe_modes": report.get("safe_modes", []),
        "generated_at": report["generated_at"],
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus-compatible metrics for supervisor and dependency state."""
    refresh_observability_metrics()
    return metrics_service.render_prometheus()


def get_admin_html() -> str:
    """Generate the admin dashboard HTML."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Local AI Chatbot Console</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        :root {
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-300: #d1d5db;
            --gray-400: #9ca3af;
            --gray-500: #6b7280;
            --gray-600: #4b5563;
            --gray-700: #374151;
            --gray-800: #1f2937;
            --gray-900: #111827;
        }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--gray-100);
            min-height: 100vh;
        }
        
        /* Layout */
        .app {
            display: flex;
            min-height: 100vh;
        }
        
        .sidebar {
            width: 250px;
            background: var(--gray-900);
            color: white;
            padding: 20px;
            position: fixed;
            height: 100vh;
            overflow-y: auto;
        }
        
        .sidebar-logo {
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--gray-700);
        }
        
        .sidebar-logo span {
            color: var(--primary);
        }
        
        .nav-item {
            display: flex;
            align-items: center;
            padding: 12px 16px;
            color: var(--gray-300);
            text-decoration: none;
            border-radius: 8px;
            margin-bottom: 4px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .nav-item:hover, .nav-item.active {
            background: var(--gray-800);
            color: white;
        }
        
        .nav-item svg {
            margin-right: 12px;
            width: 20px;
            height: 20px;
        }
        
        .main {
            flex: 1;
            margin-left: 250px;
            padding: 24px;
        }
        
        /* Header */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
        }
        
        .header h1 {
            font-size: 24px;
            color: var(--gray-900);
        }
        
        /* Cards */
        .card {
            background: white;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 24px;
        }
        
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        
        .card-title {
            font-size: 16px;
            font-weight: 600;
            color: var(--gray-900);
        }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 24px;
            margin-bottom: 24px;
        }
        
        .stat-card {
            background: white;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        
        .stat-label {
            font-size: 14px;
            color: var(--gray-500);
            margin-bottom: 8px;
        }
        
        .stat-value {
            font-size: 28px;
            font-weight: 700;
            color: var(--gray-900);
        }
        
        .stat-change {
            font-size: 13px;
            color: var(--success);
            margin-top: 4px;
        }
        
        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }
        
        .btn-primary {
            background: var(--primary);
            color: white;
        }
        
        .btn-primary:hover {
            background: var(--primary-dark);
        }
        
        .btn-secondary {
            background: var(--gray-200);
            color: var(--gray-700);
        }
        
        .btn-secondary:hover {
            background: var(--gray-300);
        }
        
        .btn-danger {
            background: var(--danger);
            color: white;
        }
        
        .btn-sm {
            padding: 6px 12px;
            font-size: 13px;
        }
        
        .btn svg {
            margin-right: 8px;
            width: 16px;
            height: 16px;
        }
        
        /* Table */
        .table {
            width: 100%;
            border-collapse: collapse;
        }
        
        .table th {
            text-align: left;
            padding: 12px 16px;
            font-size: 12px;
            font-weight: 600;
            color: var(--gray-500);
            text-transform: uppercase;
            background: var(--gray-50);
            border-bottom: 1px solid var(--gray-200);
        }
        
        .table td {
            padding: 16px;
            border-bottom: 1px solid var(--gray-100);
            font-size: 14px;
        }
        
        .table tr:hover {
            background: var(--gray-50);
        }
        
        /* Status Badge */
        .badge {
            display: inline-flex;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 500;
        }
        
        .badge-success {
            background: #dcfce7;
            color: #166534;
        }
        
        .badge-warning {
            background: #fef3c7;
            color: #92400e;
        }
        
        .badge-danger {
            background: #fee2e2;
            color: #991b1b;
        }
        
        .badge-info {
            background: #dbeafe;
            color: #1e40af;
        }
        
        /* Form */
        .form-group {
            margin-bottom: 16px;
        }
        
        .form-label {
            display: block;
            font-size: 14px;
            font-weight: 500;
            color: var(--gray-700);
            margin-bottom: 6px;
        }
        
        .form-input, .form-select, .form-textarea {
            width: 100%;
            padding: 10px 14px;
            border: 1px solid var(--gray-300);
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.2s;
        }
        
        .form-input:focus, .form-select:focus, .form-textarea:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
        }
        
        .form-textarea {
            min-height: 100px;
            resize: vertical;
        }
        
        /* Modal */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        
        .modal-overlay.show {
            display: flex;
        }
        
        .modal {
            background: white;
            border-radius: 12px;
            width: 100%;
            max-width: 600px;
            max-height: 90vh;
            overflow-y: auto;
        }
        
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 24px;
            border-bottom: 1px solid var(--gray-200);
        }
        
        .modal-title {
            font-size: 18px;
            font-weight: 600;
        }
        
        .modal-close {
            background: none;
            border: none;
            cursor: pointer;
            color: var(--gray-500);
        }
        
        .modal-body {
            padding: 24px;
        }
        
        .modal-footer {
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            padding: 16px 24px;
            border-top: 1px solid var(--gray-200);
        }
        
        /* Login Page */
        .login-page {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            background: linear-gradient(135deg, var(--gray-900) 0%, var(--gray-800) 100%);
        }
        
        .login-card {
            background: white;
            border-radius: 16px;
            padding: 40px;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        
        .login-logo {
            text-align: center;
            margin-bottom: 32px;
        }
        
        .login-logo h1 {
            font-size: 24px;
            color: var(--gray-900);
        }
        
        .login-logo span {
            color: var(--primary);
        }
        
        /* Responsive */
        @media (max-width: 1024px) {
            .stats-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        
        @media (max-width: 768px) {
            .sidebar {
                display: none;
            }
            .main {
                margin-left: 0;
            }
            .stats-grid {
                grid-template-columns: 1fr;
            }
        }
        
        /* Loading */
        .loading {
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 40px;
        }
        
        .spinner {
            width: 32px;
            height: 32px;
            border: 3px solid var(--gray-200);
            border-top-color: var(--primary);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        /* Empty state */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--gray-500);
        }
        
        .empty-state svg {
            width: 64px;
            height: 64px;
            margin-bottom: 16px;
            color: var(--gray-300);
        }
    </style>
</head>
<body>
    <div id="app"></div>
    
    <script>
        // State
        let state = {
            user: null,
            bots: [],
            templates: [],
            stats: null,
            currentView: 'dashboard',
            selectedBot: null,
            loading: true
        };
        
        // API helpers
        const API_BASE = '';
        let authToken = localStorage.getItem('auth_token');
        
        async function api(endpoint, options = {}) {
            const headers = {
                'Content-Type': 'application/json',
                ...options.headers
            };
            
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch(API_BASE + endpoint, {
                ...options,
                headers,
                credentials: 'include'
            });
            
            if (response.status === 401) {
                authToken = null;
                localStorage.removeItem('auth_token');
                render();
                throw new Error('Unauthorized');
            }
            
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.detail || 'Request failed');
            }
            
            return data;
        }
        
        // Login
        async function login(username, password) {
            const data = await api('/api/admin/login', {
                method: 'POST',
                body: JSON.stringify({ username, password })
            });
            
            authToken = data.access_token;
            localStorage.setItem('auth_token', authToken);
            
            await loadData();
            render();
        }
        
        async function logout() {
            await api('/api/admin/logout', { method: 'POST' });
            authToken = null;
            localStorage.removeItem('auth_token');
            state.user = null;
            render();
        }
        
        // Data loading
        async function loadData() {
            try {
                state.loading = true;
                render();
                
                const [user, botsData, templates, stats] = await Promise.all([
                    api('/api/admin/me'),
                    api('/api/bots'),
                    api('/api/templates'),
                    api('/api/admin/dashboard/stats')
                ]);
                
                state.user = user;
                state.bots = botsData.bots;
                state.templates = templates.templates;
                state.stats = stats;
                state.loading = false;
                
                render();
            } catch (e) {
                console.error('Failed to load data:', e);
                state.loading = false;
                state.user = null;
            }
        }
        
        // Bot operations
        async function createBot(botData) {
            const bot = await api('/api/bots', {
                method: 'POST',
                body: JSON.stringify(botData)
            });
            state.bots.unshift(bot);
            render();
            return bot;
        }
        
        async function updateBot(botId, updates) {
            const bot = await api(`/api/bots/${botId}`, {
                method: 'PUT',
                body: JSON.stringify(updates)
            });
            const idx = state.bots.findIndex(b => b.id === botId);
            if (idx >= 0) state.bots[idx] = bot;
            render();
            return bot;
        }
        
        async function deleteBot(botId) {
            if (!confirm('Are you sure you want to delete this bot?')) return;
            await api(`/api/bots/${botId}`, { method: 'DELETE' });
            state.bots = state.bots.filter(b => b.id !== botId);
            render();
        }
        
        async function toggleBot(botId) {
            const bot = await api(`/api/bots/${botId}/toggle`, { method: 'POST' });
            const idx = state.bots.findIndex(b => b.id === botId);
            if (idx >= 0) state.bots[idx] = bot;
            render();
        }
        
        async function cloneBot(botId, newName) {
            const bot = await api(`/api/bots/${botId}/clone`, {
                method: 'POST',
                body: JSON.stringify({ new_name: newName })
            });
            state.bots.unshift(bot);
            render();
            return bot;
        }
        
        // Render functions
        function render() {
            const app = document.getElementById('app');
            
            if (!authToken || !state.user) {
                app.innerHTML = renderLogin();
            } else if (state.loading) {
                app.innerHTML = renderLoading();
            } else {
                app.innerHTML = renderApp();
            }
            
            attachEventListeners();
        }
        
        function renderLogin() {
            return `
                <div class="login-page">
                    <div class="login-card">
                        <div class="login-logo">
                            <h1>Local <span>AI</span> Console</h1>
                            <p style="color: var(--gray-500); margin-top: 8px;">Sign in to your account</p>
                        </div>
                        <form id="login-form">
                            <div class="form-group">
                                <label class="form-label">Username</label>
                                <input type="text" class="form-input" id="login-username" placeholder="admin" required>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Password</label>
                                <input type="password" class="form-input" id="login-password" placeholder="â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢" required>
                            </div>
                            <button type="submit" class="btn btn-primary" style="width: 100%; margin-top: 16px;">
                                Sign In
                            </button>
                            <p style="text-align: center; margin-top: 16px; font-size: 13px; color: var(--gray-500);">
                                Default: admin / (check .env file)
                            </p>
                        </form>
                    </div>
                </div>
            `;
        }
        
        function renderLoading() {
            return `
                <div class="loading" style="min-height: 100vh;">
                    <div class="spinner"></div>
                </div>
            `;
        }
        
        function renderApp() {
            return `
                <div class="app">
                    ${renderSidebar()}
                    <div class="main">
                        ${state.currentView === 'dashboard' ? renderDashboard() : ''}
                        ${state.currentView === 'bots' ? renderBots() : ''}
                        ${state.currentView === 'bot-detail' ? renderBotDetail() : ''}
                        ${state.currentView === 'templates' ? renderTemplates() : ''}
                        ${state.currentView === 'scraper' ? renderScraper() : ''}
                    </div>
                </div>
                ${renderModals()}
            `;
        }
        
        function renderSidebar() {
            return `
                <div class="sidebar">
                    <div class="sidebar-logo">Local <span>AI</span> Console</div>
                    <nav>
                        <a class="nav-item ${state.currentView === 'dashboard' ? 'active' : ''}" data-view="dashboard">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
                                <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
                            </svg>
                            Dashboard
                        </a>
                        <a class="nav-item ${state.currentView === 'bots' || state.currentView === 'bot-detail' ? 'active' : ''}" data-view="bots">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                            </svg>
                            Chatbots
                        </a>
                        <a class="nav-item ${state.currentView === 'templates' ? 'active' : ''}" data-view="templates">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                <polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/>
                                <line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
                            </svg>
                            Templates
                        </a>
                        <a class="nav-item ${state.currentView === 'scraper' ? 'active' : ''}" data-view="scraper">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>
                                <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                            </svg>
                            Web Scraper
                        </a>
                    </nav>
                    <div style="position: absolute; bottom: 20px; left: 20px; right: 20px;">
                        <div style="color: var(--gray-400); font-size: 13px; margin-bottom: 12px;">
                            ${state.user?.username || 'Admin'}
                        </div>
                        <button class="btn btn-secondary btn-sm" style="width: 100%;" onclick="logout()">
                            Logout
                        </button>
                    </div>
                </div>
            `;
        }
        
        function renderDashboard() {
            const s = state.stats || {};
            return `
                <div class="header">
                    <h1>Dashboard</h1>
                    <button class="btn btn-primary" onclick="showModal('create-bot')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                        </svg>
                        New Bot
                    </button>
                </div>
                
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">Total Bots</div>
                        <div class="stat-value">${s.total_bots || 0}</div>
                        <div class="stat-change">${s.active_bots || 0} active</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Messages (30d)</div>
                        <div class="stat-value">${(s.total_messages_30d || 0).toLocaleString()}</div>
                        <div class="stat-change">${s.messages_today || 0} today</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Tokens Used (30d)</div>
                        <div class="stat-value">${((s.total_tokens_30d || 0) / 1000).toFixed(1)}K</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Avg Latency</div>
                        <div class="stat-value">${s.avg_latency_ms || 0}ms</div>
                    </div>
                </div>
                
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">Recent Bots</span>
                        <a class="btn btn-secondary btn-sm" data-view="bots">View All</a>
                    </div>
                    ${renderBotsTable(state.bots.slice(0, 5))}
                </div>
            `;
        }
        
        function renderBots() {
            return `
                <div class="header">
                    <h1>Chatbots</h1>
                    <button class="btn btn-primary" onclick="showModal('create-bot')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                        </svg>
                        New Bot
                    </button>
                </div>
                
                <div class="card">
                    ${state.bots.length === 0 ? `
                        <div class="empty-state">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                            </svg>
                            <h3>No bots yet</h3>
                            <p>Create your first chatbot to get started</p>
                        </div>
                    ` : renderBotsTable(state.bots)}
                </div>
            `;
        }
        
        function renderBotsTable(bots) {
            if (bots.length === 0) return '<p style="padding: 20px; color: var(--gray-500);">No bots found</p>';
            
            return `
                <table class="table">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Provider</th>
                            <th>Status</th>
                            <th>Messages</th>
                            <th>Memory</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${bots.map(bot => `
                            <tr>
                                <td>
                                    <strong>${bot.name}</strong>
                                    <div style="font-size: 12px; color: var(--gray-500);">${bot.id.substring(0, 8)}...</div>
                                </td>
                                <td><span class="badge badge-info">${bot.provider_type}</span></td>
                                <td>
                                    ${bot.is_available ? 
                                        '<span class="badge badge-success">Active</span>' : 
                                        '<span class="badge badge-danger">Inactive</span>'
                                    }
                                </td>
                                <td>${bot.messages_used}${bot.message_limit ? '/' + bot.message_limit : ''}</td>
                                <td>${bot.memory_enabled ? 'Yes' : 'No'}</td>
                                <td>
                                    <button class="btn btn-secondary btn-sm" onclick="viewBot('${bot.id}')">Edit</button>
                                    <button class="btn btn-secondary btn-sm" onclick="toggleBot('${bot.id}')">${bot.is_active ? 'Disable' : 'Enable'}</button>
                                    <button class="btn btn-sm" style="background: var(--danger); color: white;" onclick="deleteBot('${bot.id}')">Delete</button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        }
        
        function renderBotDetail() {
            const bot = state.selectedBot;
            if (!bot) return '<p>Bot not found</p>';
            
            const embedUrl = `${window.location.origin}/embed/${bot.id}`;
            const isLocal = bot.provider_type === 'local';
            
            return `
                <div class="header">
                    <h1>
                        <a data-view="bots" style="color: var(--gray-400); cursor: pointer;">Chatbots</a> / ${bot.name}
                    </h1>
                    <div>
                        <button class="btn btn-secondary" onclick="showCloneModal('${bot.id}')">Clone</button>
                        <button class="btn btn-primary" onclick="saveBot()">Save Changes</button>
                    </div>
                </div>
                
                <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 24px;">
                    <div>
                        <div class="card">
                            <h3 class="card-title" style="margin-bottom: 16px;">Configuration</h3>
                            <div class="form-group">
                                <label class="form-label">Name</label>
                                <input type="text" class="form-input" id="bot-name" value="${bot.name}">
                            </div>
                            ${isLocal ? `
                            <div class="form-group">
                                <label class="form-label">Local Model</label>
                                <select class="form-select" id="bot-local-model">
                                    <option value="qwen2.5:1.5b" ${bot.model === 'qwen2.5:1.5b' ? 'selected' : ''}>Qwen 2.5 1.5B (Low VRAM ~2GB)</option>
                                    <option value="mimo-v2-flash" ${bot.model === 'mimo-v2-flash' ? 'selected' : ''}>MiMo-V2-Flash (Low VRAM ~3GB)</option>
                                    <option value="llama3.2:1b" ${bot.model === 'llama3.2:1b' ? 'selected' : ''}>Llama 3.2 1B (Ultra Low ~1GB)</option>
                                    <option value="llama3.2:3b" ${bot.model === 'llama3.2:3b' ? 'selected' : ''}>Llama 3.2 3B (Low ~3GB)</option>
                                    <option value="phi3:mini" ${bot.model === 'phi3:mini' ? 'selected' : ''}>Phi-3 Mini (Low ~4GB)</option>
                                    <option value="gemma2:2b" ${bot.model === 'gemma2:2b' ? 'selected' : ''}>Gemma 2 2B (Low ~2GB)</option>
                                    <option value="custom" ${!['qwen2.5:1.5b','mimo-v2-flash','llama3.2:1b','llama3.2:3b','phi3:mini','gemma2:2b'].includes(bot.model) ? 'selected' : ''}>Custom Model</option>
                                </select>
                                <input type="text" class="form-input" id="bot-custom-model" placeholder="Enter custom model name" value="${bot.model || ''}" style="margin-top: 8px; ${['qwen2.5:1.5b','mimo-v2-flash','llama3.2:1b','llama3.2:3b','phi3:mini','gemma2:2b'].includes(bot.model) ? 'display:none;' : ''}">
                            </div>
                            ` : ''}
                            <div class="form-group">
                                <label class="form-label">System Prompt</label>
                                <textarea class="form-textarea" id="bot-prompt" rows="5">${bot.system_prompt || ''}</textarea>
                            </div>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                                <div class="form-group">
                                    <label class="form-label">Temperature</label>
                                    <input type="number" class="form-input" id="bot-temp" value="${bot.temperature}" min="0" max="2" step="0.1">
                                </div>
                                <div class="form-group">
                                    <label class="form-label">Max Tokens</label>
                                    <input type="number" class="form-input" id="bot-tokens" value="${bot.max_tokens}">
                                </div>
                            </div>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                                <div class="form-group">
                                    <label class="form-label">Message Limit</label>
                                    <input type="number" class="form-input" id="bot-limit" value="${bot.message_limit || ''}" placeholder="Unlimited">
                                </div>
                                <div class="form-group">
                                    <label class="form-label">Accent Color</label>
                                    <input type="color" class="form-input" id="bot-color" value="${bot.accent_color}" style="height: 42px;">
                                </div>
                            </div>
                            <div class="form-group">
                                <label style="display: flex; align-items: center; gap: 8px;">
                                    <input type="checkbox" id="bot-memory" ${bot.memory_enabled ? 'checked' : ''}>
                                    Enable Conversation Memory
                                </label>
                            </div>
                        </div>
                        
                        <div class="card">
                            <h3 class="card-title" style="margin-bottom: 16px;">Knowledge Base</h3>
                            <div class="form-group">
                                <label class="form-label">Upload Content</label>
                                <textarea class="form-textarea" id="kb-content" placeholder="Paste text content here..."></textarea>
                            </div>
                            <div style="display: flex; gap: 8px;">
                                <button class="btn btn-primary" onclick="uploadKnowledge('${bot.id}', false)">Replace Knowledge</button>
                                <button class="btn btn-secondary" onclick="uploadKnowledge('${bot.id}', true)">Append</button>
                                <button class="btn btn-secondary" onclick="rebuildEmbeddings('${bot.id}')">Rebuild Embeddings</button>
                            </div>
                        </div>
                        
                        <div class="card">
                            <h3 class="card-title" style="margin-bottom: 16px;">Scrape Website</h3>
                            <p style="font-size: 13px; color: var(--gray-500); margin-bottom: 12px;">
                                Import content from a website directly into this bot's knowledge base.
                            </p>
                            <div class="form-group">
                                <label class="form-label">Website URL</label>
                                <input type="url" class="form-input" id="scrape-url" placeholder="https://example.com">
                            </div>
                            <div class="form-group">
                                <label class="form-label">Crawl Depth</label>
                                <select class="form-select" id="scrape-depth">
                                    <option value="1">1 - Homepage only</option>
                                    <option value="2" selected>2 - Homepage + linked pages</option>
                                    <option value="3">3 - Deep crawl</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label style="display: flex; align-items: center; gap: 8px;">
                                    <input type="checkbox" id="scrape-consent">
                                    I confirm that I have permission to scrape this website
                                </label>
                            </div>
                            <div style="display: flex; gap: 8px;">
                                <button class="btn btn-primary" onclick="scrapeForBot('${bot.id}', false)">Scrape &amp; Replace</button>
                                <button class="btn btn-secondary" onclick="scrapeForBot('${bot.id}', true)">Scrape &amp; Append</button>
                            </div>
                            <div id="scrape-status" style="margin-top: 12px; display: none;"></div>
                        </div>
                        
                        <div class="card">
                            <h3 class="card-title" style="margin-bottom: 16px;">Review Knowledge Base</h3>
                            <p style="font-size: 13px; color: var(--gray-500); margin-bottom: 12px;">
                                View and manage scraped/uploaded content for this bot.
                            </p>
                            <div class="form-group">
                                <input type="text" class="form-input" id="kb-search" placeholder="Search knowledge..." onkeyup="if(event.key==='Enter')loadKnowledgeChunks('${bot.id}', 1)">
                            </div>
                            <div style="display: flex; gap: 8px; margin-bottom: 12px;">
                                <button class="btn btn-primary" onclick="loadKnowledgeChunks('${bot.id}', 1)">Load Knowledge</button>
                                <button class="btn btn-secondary" onclick="clearKnowledgeView()">Clear View</button>
                            </div>
                            <div id="kb-stats" style="font-size: 13px; color: var(--gray-500); margin-bottom: 12px;"></div>
                            <div id="kb-chunks-container" style="max-height: 400px; overflow-y: auto;">
                                <div id="kb-chunks-list"></div>
                            </div>
                            <div id="kb-pagination" style="margin-top: 12px; display: flex; justify-content: space-between; align-items: center;"></div>
                        </div>
                    </div>
                    
                    <div>
                        <div class="card">
                            <h3 class="card-title" style="margin-bottom: 16px;">Status</h3>
                            <div style="margin-bottom: 16px;">
                                <span class="${bot.is_available ? 'badge badge-success' : 'badge badge-danger'}">
                                    ${bot.is_available ? 'Active' : 'Inactive'}
                                </span>
                            </div>
                            <p style="font-size: 14px; color: var(--gray-600);">
                                Provider: <strong>${bot.provider_type}</strong><br>
                                Template: <strong>${bot.template_type}</strong><br>
                                ${bot.model ? `Model: <strong>${bot.model}</strong><br>` : ''}
                                Messages: <strong>${bot.messages_used}</strong><br>
                                Created: <strong>${new Date(bot.created_at).toLocaleDateString()}</strong>
                            </p>
                        </div>
                        
                        <div class="card">
                            <h3 class="card-title" style="margin-bottom: 16px;">Embed Code</h3>
                            <p style="font-size: 13px; color: var(--gray-600); margin-bottom: 12px;">
                                Copy this code to embed the chat widget:
                            </p>
                            <textarea class="form-textarea" style="font-family: monospace; font-size: 12px;" readonly>&lt;iframe src="${embedUrl}" width="100%" height="600" frameborder="0"&gt;&lt;/iframe&gt;</textarea>
                            <a href="${embedUrl}" target="_blank" class="btn btn-secondary btn-sm" style="margin-top: 8px;">Preview Widget</a>
                        </div>
                    </div>
                </div>
            `;
        }
        
        function renderTemplates() {
            return `
                <div class="header">
                    <h1>Templates</h1>
                </div>
                
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px;">
                    ${state.templates.map(t => `
                        <div class="card">
                            <h3 class="card-title">${t.name}</h3>
                            <p style="color: var(--gray-500); font-size: 14px; margin-top: 8px;">
                                Provider: ${t.provider}
                            </p>
                            <div style="margin-top: 16px;">
                                <span class="badge badge-info">Model: ${t.default_model || 'default'}</span>
                                ${t.supports_memory ? '<span class="badge badge-success" style="margin-left: 4px;">Memory</span>' : ''}
                            </div>
                            <div style="margin-top: 16px; font-size: 13px; color: var(--gray-600);">
                                <strong>Required:</strong> ${t.required_fields.join(', ') || 'None'}
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        }
        
        function renderScraper() {
            return `
                <div class="header">
                    <h1>Web Scraper</h1>
                </div>
                
                <div class="card">
                    <h3 class="card-title" style="margin-bottom: 16px;">Create Bot from Website</h3>
                    <form id="scraper-form">
                        <div class="form-group">
                            <label class="form-label">Website URL</label>
                            <input type="url" class="form-input" id="scraper-url" placeholder="https://example.com" required>
                        </div>
                        <div class="form-group">
                            <label class="form-label">Crawl Depth</label>
                            <select class="form-select" id="scraper-depth">
                                <option value="1">1 - Homepage only</option>
                                <option value="2" selected>2 - Homepage + linked pages</option>
                                <option value="3">3 - Deep crawl</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label style="display: flex; align-items: center; gap: 8px;">
                                <input type="checkbox" id="scraper-consent" required>
                                I confirm that I have permission to scrape this website
                            </label>
                        </div>
                        <button type="submit" class="btn btn-primary">Start Scraping</button>
                    </form>
                </div>
                
                <div class="card" id="scraper-result" style="display: none;">
                    <h3 class="card-title" style="margin-bottom: 16px;">Scraping Result</h3>
                    <div id="scraper-content"></div>
                </div>
            `;
        }
        
        function renderModals() {
            return `
                <!-- Create Bot Modal -->
                <div class="modal-overlay" id="modal-create-bot">
                    <div class="modal">
                        <div class="modal-header">
                            <span class="modal-title">Create New Bot</span>
                            <button class="modal-close" onclick="hideModal('create-bot')">&times;</button>
                        </div>
                        <div class="modal-body">
                            <div class="form-group">
                                <label class="form-label">Bot Name</label>
                                <input type="text" class="form-input" id="new-bot-name" placeholder="My Assistant">
                            </div>
                            <div class="form-group">
                                <label class="form-label">Template</label>
                                <select class="form-select" id="new-bot-template">
                                    ${state.templates.map(t => `
                                        <option value="${t.name.toLowerCase().replace(/ /g, '_')}_template">${t.name}</option>
                                    `).join('')}
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Provider</label>
                                <select class="form-select" id="new-bot-provider" onchange="toggleLocalModelOptions()">
                                    <option value="openai">OpenAI</option>
                                    <option value="local">Local LLM</option>
                                    <option value="claude">Claude</option>
                                </select>
                            </div>
                            <div class="form-group" id="local-model-group" style="display: none;">
                                <label class="form-label">Local Model</label>
                                <select class="form-select" id="new-bot-local-model" onchange="toggleCustomModelInput()">
                                    <option value="qwen2.5:1.5b">Qwen 2.5 1.5B (Low VRAM ~2GB)</option>
                                    <option value="mimo-v2-flash">MiMo-V2-Flash (Low VRAM ~3GB)</option>
                                    <option value="llama3.2:1b">Llama 3.2 1B (Ultra Low ~1GB)</option>
                                    <option value="llama3.2:3b">Llama 3.2 3B (Low ~3GB)</option>
                                    <option value="phi3:mini">Phi-3 Mini (Low ~4GB)</option>
                                    <option value="gemma2:2b">Gemma 2 2B (Low ~2GB)</option>
                                    <option value="custom">Custom Model</option>
                                </select>
                                <input type="text" class="form-input" id="new-bot-custom-model" placeholder="Enter custom model name (e.g., mistral:7b)" style="margin-top: 8px; display: none;">
                                <p style="font-size: 12px; color: var(--gray-500); margin-top: 8px;">
                                    Requires Ollama running locally. Install from <a href="https://ollama.ai" target="_blank">ollama.ai</a>
                                </p>
                            </div>
                            <div class="form-group" id="api-key-group">
                                <label class="form-label">API Key (optional)</label>
                                <input type="password" class="form-input" id="new-bot-key" placeholder="sk-...">
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button class="btn btn-secondary" onclick="hideModal('create-bot')">Cancel</button>
                            <button class="btn btn-primary" onclick="submitCreateBot()">Create Bot</button>
                        </div>
                    </div>
                </div>
                
                <!-- Clone Bot Modal -->
                <div class="modal-overlay" id="modal-clone-bot">
                    <div class="modal">
                        <div class="modal-header">
                            <span class="modal-title">Clone Bot</span>
                            <button class="modal-close" onclick="hideModal('clone-bot')">&times;</button>
                        </div>
                        <div class="modal-body">
                            <div class="form-group">
                                <label class="form-label">New Bot Name</label>
                                <input type="text" class="form-input" id="clone-bot-name" placeholder="Copy of...">
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button class="btn btn-secondary" onclick="hideModal('clone-bot')">Cancel</button>
                            <button class="btn btn-primary" onclick="submitCloneBot()">Clone Bot</button>
                        </div>
                    </div>
                </div>
            `;
        }
        
        // Modal functions
        let cloneBotId = null;
        
        function showModal(id) {
            document.getElementById('modal-' + id).classList.add('show');
        }
        
        function hideModal(id) {
            document.getElementById('modal-' + id).classList.remove('show');
        }
        
        function showCloneModal(botId) {
            cloneBotId = botId;
            showModal('clone-bot');
        }
        
        function toggleLocalModelOptions() {
            const provider = document.getElementById('new-bot-provider').value;
            const localModelGroup = document.getElementById('local-model-group');
            const apiKeyGroup = document.getElementById('api-key-group');
            
            if (provider === 'local') {
                localModelGroup.style.display = 'block';
                apiKeyGroup.style.display = 'none';
            } else {
                localModelGroup.style.display = 'none';
                apiKeyGroup.style.display = 'block';
            }
        }
        
        function toggleCustomModelInput() {
            const modelSelect = document.getElementById('new-bot-local-model');
            const customInput = document.getElementById('new-bot-custom-model');
            
            if (modelSelect.value === 'custom') {
                customInput.style.display = 'block';
            } else {
                customInput.style.display = 'none';
            }
        }
        
        async function submitCreateBot() {
            const name = document.getElementById('new-bot-name').value;
            const template = document.getElementById('new-bot-template').value;
            const provider = document.getElementById('new-bot-provider').value;
            const apiKey = document.getElementById('new-bot-key').value;
            
            // Get model for local provider
            let model = undefined;
            if (provider === 'local') {
                const modelSelect = document.getElementById('new-bot-local-model');
                if (modelSelect.value === 'custom') {
                    model = document.getElementById('new-bot-custom-model').value || 'qwen2.5:1.5b';
                } else {
                    model = modelSelect.value;
                }
            }
            
            if (!name) {
                alert('Please enter a bot name');
                return;
            }
            
            try {
                await createBot({
                    name,
                    template_type: template,
                    provider_type: provider,
                    api_key: apiKey || undefined,
                    model: model
                });
                hideModal('create-bot');
                await loadData();
            } catch (e) {
                alert('Failed to create bot: ' + e.message);
            }
        }
        
        async function submitCloneBot() {
            const name = document.getElementById('clone-bot-name').value;
            
            if (!name) {
                alert('Please enter a name for the cloned bot');
                return;
            }
            
            try {
                await cloneBot(cloneBotId, name);
                hideModal('clone-bot');
            } catch (e) {
                alert('Failed to clone bot: ' + e.message);
            }
        }
        
        // View functions
        function viewBot(botId) {
            state.selectedBot = state.bots.find(b => b.id === botId);
            state.currentView = 'bot-detail';
            render();
        }
        
        async function saveBot() {
            const bot = state.selectedBot;
            if (!bot) return;
            
            // Get model for local provider
            let model = bot.model;
            if (bot.provider_type === 'local') {
                const modelSelect = document.getElementById('bot-local-model');
                const customModel = document.getElementById('bot-custom-model');
                if (modelSelect && modelSelect.value === 'custom') {
                    model = customModel ? customModel.value : bot.model;
                } else if (modelSelect) {
                    model = modelSelect.value;
                }
            }
            
            try {
                await updateBot(bot.id, {
                    name: document.getElementById('bot-name').value,
                    system_prompt: document.getElementById('bot-prompt').value,
                    temperature: parseFloat(document.getElementById('bot-temp').value),
                    max_tokens: parseInt(document.getElementById('bot-tokens').value),
                    message_limit: document.getElementById('bot-limit').value ? parseInt(document.getElementById('bot-limit').value) : null,
                    accent_color: document.getElementById('bot-color').value,
                    memory_enabled: document.getElementById('bot-memory').checked,
                    model: model
                });
                alert('Bot saved successfully!');
            } catch (e) {
                alert('Failed to save: ' + e.message);
            }
        }
        
        async function uploadKnowledge(botId, append) {
            const content = document.getElementById('kb-content').value;
            if (!content) {
                alert('Please enter some content');
                return;
            }
            
            try {
                const formData = new FormData();
                formData.append('content', content);
                formData.append('source', 'manual_upload');
                formData.append('append', append);
                
                const response = await fetch(`/api/bots/${botId}/knowledge`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${authToken}`
                    },
                    body: formData
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    alert('Knowledge uploaded! Chunks created: ' + data.chunks_created);
                    document.getElementById('kb-content').value = '';
                } else {
                    alert('Failed: ' + data.detail);
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        async function rebuildEmbeddings(botId) {
            try {
                const data = await api(`/api/bots/${botId}/rebuild-embeddings`, { method: 'POST' });
                alert('Embeddings rebuilt! Chunks processed: ' + data.chunks_processed);
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        async function scrapeForBot(botId, append) {
            const url = document.getElementById('scrape-url').value;
            const depth = document.getElementById('scrape-depth').value;
            const consent = document.getElementById('scrape-consent').checked;
            const statusDiv = document.getElementById('scrape-status');
            
            if (!url) {
                alert('Please enter a URL');
                return;
            }
            
            if (!consent) {
                alert('You must confirm you have permission to scrape this website');
                return;
            }
            
            statusDiv.innerHTML = '<p style="color: var(--primary);">Scraping in progress... This may take a minute.</p>';
            statusDiv.style.display = 'block';
            
            try {
                const formData = new FormData();
                formData.append('url', url);
                formData.append('depth', depth);
                formData.append('consent_confirmed', 'true');
                formData.append('append', append);
                
                const response = await fetch(`/api/bots/${botId}/scrape`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${authToken}`
                    },
                    body: formData
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    statusDiv.innerHTML = `<p style="color: var(--success);">Success! Pages scraped: ${data.pages_scraped}, Chunks created: ${data.chunks_created}</p>`;
                    document.getElementById('scrape-url').value = '';
                    document.getElementById('scrape-consent').checked = false;
                } else {
                    statusDiv.innerHTML = `<p style="color: var(--danger);">Error: ${data.detail}</p>`;
                }
            } catch (e) {
                statusDiv.innerHTML = `<p style="color: var(--danger);">Error: ${e.message}</p>`;
            }
        }
        
        // Knowledge Base Viewer Functions
        let currentKnowledgeBotId = null;
        let currentKnowledgePage = 1;
        
        async function loadKnowledgeChunks(botId, page = 1) {
            currentKnowledgeBotId = botId;
            currentKnowledgePage = page;
            
            const search = document.getElementById('kb-search')?.value || '';
            const listContainer = document.getElementById('kb-chunks-list');
            const statsDiv = document.getElementById('kb-stats');
            const paginationDiv = document.getElementById('kb-pagination');
            
            listContainer.innerHTML = '<p style="color: var(--gray-500);">Loading...</p>';
            
            try {
                const params = new URLSearchParams({
                    page: page,
                    per_page: 10
                });
                if (search) params.append('search', search);
                
                const response = await fetch(`/api/bots/${botId}/knowledge/chunks?${params}`, {
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to load knowledge');
                }
                
                // Update stats
                statsDiv.innerHTML = `Found <strong>${data.pagination.total_items}</strong> chunks (Page ${data.pagination.page} of ${data.pagination.total_pages})`;
                
                // Render chunks
                if (data.chunks.length === 0) {
                    listContainer.innerHTML = '<p style="color: var(--gray-500); padding: 16px; text-align: center;">No knowledge chunks found. Upload content or scrape a website to add knowledge.</p>';
                } else {
                    listContainer.innerHTML = data.chunks.map(chunk => `
                        <div class="kb-chunk" style="border: 1px solid var(--gray-200); border-radius: 8px; padding: 12px; margin-bottom: 8px; background: var(--gray-50);">
                            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                                <div style="font-size: 11px; color: var(--gray-400);">
                                    ${chunk.source ? `Source: <strong>${chunk.source}</strong><br>` : ''}
                                    ${chunk.created_at ? `Added: ${new Date(chunk.created_at).toLocaleString()}` : ''}
                                    ${chunk.word_count ? ` (${chunk.word_count} words)` : ''}
                                </div>
                                <button class="btn btn-danger btn-sm" onclick="deleteKnowledgeChunk('${botId}', '${chunk.id}')" style="font-size: 11px; padding: 4px 8px;">Delete</button>
                            </div>
                            <div style="font-size: 13px; color: var(--gray-700); white-space: pre-wrap; word-break: break-word; max-height: 150px; overflow-y: auto;">${escapeHtml(chunk.content)}</div>
                            ${chunk.content.endsWith('...') ? '<button class="btn btn-secondary btn-sm" style="margin-top: 8px; font-size: 11px;" onclick="showFullChunk(\\'' + chunk.id + '\\', \\'' + botId + '\\')">Show Full Content</button>' : ''}
                        </div>
                    `).join('');
                }
                
                // Render pagination
                const { page: currentPage, total_pages } = data.pagination;
                let paginationHtml = '';
                if (total_pages > 1) {
                    paginationHtml = `
                        <button class="btn btn-secondary btn-sm" ${currentPage <= 1 ? 'disabled' : ''} onclick="loadKnowledgeChunks('${botId}', ${currentPage - 1})">â† Previous</button>
                        <span style="font-size: 13px;">Page ${currentPage} of ${total_pages}</span>
                        <button class="btn btn-secondary btn-sm" ${currentPage >= total_pages ? 'disabled' : ''} onclick="loadKnowledgeChunks('${botId}', ${currentPage + 1})">Next â†’</button>
                    `;
                }
                paginationDiv.innerHTML = paginationHtml;
                
            } catch (e) {
                listContainer.innerHTML = `<p style="color: var(--danger);">Error: ${e.message}</p>`;
                statsDiv.innerHTML = '';
                paginationDiv.innerHTML = '';
            }
        }
        
        async function deleteKnowledgeChunk(botId, chunkId) {
            if (!confirm('Are you sure you want to delete this knowledge chunk?')) {
                return;
            }
            
            try {
                const response = await fetch(`/api/bots/${botId}/knowledge/chunks/${chunkId}`, {
                    method: 'DELETE',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Failed to delete chunk');
                }
                
                // Reload the current page
                loadKnowledgeChunks(botId, currentKnowledgePage);
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        function clearKnowledgeView() {
            document.getElementById('kb-chunks-list').innerHTML = '';
            document.getElementById('kb-stats').innerHTML = '';
            document.getElementById('kb-pagination').innerHTML = '';
            const searchInput = document.getElementById('kb-search');
            if (searchInput) searchInput.value = '';
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Event listeners
        function attachEventListeners() {
            // Login form
            const loginForm = document.getElementById('login-form');
            if (loginForm) {
                loginForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const username = document.getElementById('login-username').value;
                    const password = document.getElementById('login-password').value;
                    try {
                        await login(username, password);
                    } catch (e) {
                        alert('Login failed: ' + e.message);
                    }
                });
            }
            
            // Navigation
            document.querySelectorAll('[data-view]').forEach(el => {
                el.addEventListener('click', (e) => {
                    e.preventDefault();
                    state.currentView = el.dataset.view;
                    render();
                });
            });
            
            // Scraper form
            const scraperForm = document.getElementById('scraper-form');
            if (scraperForm) {
                scraperForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const url = document.getElementById('scraper-url').value;
                    const depth = document.getElementById('scraper-depth').value;
                    const consent = document.getElementById('scraper-consent').checked;
                    
                    try {
                        const data = await api('/api/scraper/start', {
                            method: 'POST',
                            body: JSON.stringify({ url, depth: parseInt(depth), consent_confirmed: consent })
                        });
                        
                        document.getElementById('scraper-result').style.display = 'block';
                        document.getElementById('scraper-content').innerHTML = `
                            <p>Job started! ID: ${data.id}</p>
                            <p>Status: ${data.status}</p>
                        `;
                    } catch (e) {
                        alert('Scraper error: ' + e.message);
                    }
                });
            }
            
            // Local model dropdown toggle for bot detail page
            const botLocalModel = document.getElementById('bot-local-model');
            if (botLocalModel) {
                botLocalModel.addEventListener('change', function() {
                    const customInput = document.getElementById('bot-custom-model');
                    if (customInput) {
                        customInput.style.display = this.value === 'custom' ? 'block' : 'none';
                    }
                });
            }
        }
        
        // Initialize
        if (authToken) {
            loadData();
        } else {
            render();
        }
    </script>
</body>
</html>'''


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )


