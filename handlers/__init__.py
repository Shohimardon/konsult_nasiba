from __future__ import annotations

from .admin import router as admin_router
from .approval import router as approval_router
from .business import router as business_router

# Порядок: сперва команды/колбэки/FSM в обычном чате, затем Business-сообщения.
routers = [admin_router, approval_router, business_router]
