from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.bikes import router as bikes_router
from app.api.v1.chat import router as chat_router
from app.api.v1.coach import router as coach_router
from app.api.v1.health import router as health_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.profile import router as profile_router
from app.api.v1.rides import router as rides_router
from app.api.v1.routes import router as routes_router
from app.api.v1.social import router as social_router
from app.api.v1.teams import router as teams_router
from app.api.v1.training import router as training_router
from app.api.v1.training import workout_router

v1 = APIRouter()
v1.include_router(health_router)
v1.include_router(auth_router)
v1.include_router(profile_router)
v1.include_router(bikes_router)
v1.include_router(rides_router)
v1.include_router(routes_router)
v1.include_router(social_router)
v1.include_router(teams_router)
v1.include_router(chat_router)
v1.include_router(notifications_router)
v1.include_router(training_router)
v1.include_router(workout_router)
v1.include_router(coach_router)
