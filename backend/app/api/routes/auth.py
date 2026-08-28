"""Accounts and profile.

Signing in is optional - it exists so a user's filings and history follow
them across sessions and devices. Nothing in the app requires it.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, Users
from app.api.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    SignupRequest,
    UpdateProfileRequest,
    UserOut,
)
from app.auth import create_access_token, hash_password, verify_password
from app.config import settings
from app.domain.models import User
from app.exceptions import AuthenticationError, ConflictError

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(user: User) -> AuthResponse:
    token = create_access_token(
        user.id, settings.jwt_secret, settings.jwt_algorithm, settings.jwt_expires_minutes
    )
    return AuthResponse(token=token, user=UserOut.of(user))


@router.post("/signup", response_model=AuthResponse)
def signup(payload: SignupRequest, users: Users):
    if users.get_by_email(payload.email):
        raise ConflictError("An account with this email already exists.")
    user = users.create(payload.email, payload.display_name, hash_password(payload.password))
    return _issue(user)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, users: Users):
    record = users.get_password_hash(payload.email.lower().strip())
    # Same message either way: distinguishing "no such account" from "wrong
    # password" tells an attacker which emails are registered.
    if not record or not verify_password(payload.password, record[1]):
        raise AuthenticationError("Incorrect email or password.")
    return _issue(users.get(record[0]))


@router.get("/me", response_model=UserOut | None)
def me(user: CurrentUser):
    return UserOut.of(user) if user else None


@router.patch("/me", response_model=UserOut)
def update_profile(payload: UpdateProfileRequest, user: CurrentUser, users: Users):
    if user is None:
        raise AuthenticationError("Sign in to update your profile.")
    return UserOut.of(users.update_display_name(user.id, payload.display_name))


@router.post("/me/password", status_code=204)
def change_password(payload: ChangePasswordRequest, user: CurrentUser, users: Users):
    if user is None:
        raise AuthenticationError("Sign in to change your password.")

    # Re-authenticate before changing credentials, so a borrowed session
    # can't lock the real owner out.
    record = users.get_password_hash(user.email)
    if not record or not verify_password(payload.current_password, record[1]):
        raise AuthenticationError("Your current password is incorrect.")

    users.update_password(user.id, hash_password(payload.new_password))
