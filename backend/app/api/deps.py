"""FastAPI dependencies - the application's composition root.

Everything the routes need is constructed here and injected, rather than
reached for through `request.app.state` inside handlers. That keeps routes
testable (override a dependency, no patching) and makes the object graph
explicit in one file.

Authentication is optional throughout: `current_user` returns None for a
signed-out visitor rather than rejecting the request, because the app is
usable without an account. Signed-out visitors share one guest workspace;
signed-in users get a private one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from psycopg_pool import ConnectionPool

from app.auth import decode_access_token
from app.config import settings
from app.db.repositories.chat import ChatRepository
from app.db.repositories.filings import FilingRepository
from app.db.repositories.users import UserRepository
from app.domain.models import User
from app.ingestion.embedder import Embedder
from app.ingestion.pipeline import IngestionPipeline
from app.qa.answer_service import AnswerService
from app.qa.chat_service import ChatService
from app.qa.llm_client import LLMClient
from app.retrieval.qdrant_store import VectorStore
from app.retrieval.reranker import Reranker
from app.storage import FileStore


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


def get_retriever_cache(request: Request) -> dict:
    return request.app.state.retriever_cache


def get_embedder() -> Embedder:
    return Embedder()


def get_reranker() -> Reranker:
    return Reranker()


def get_llm_client() -> LLMClient:
    return LLMClient()


def get_file_store() -> FileStore:
    return FileStore()


def get_filing_repo(pool: Annotated[ConnectionPool, Depends(get_pool)]) -> FilingRepository:
    return FilingRepository(pool)


def get_user_repo(pool: Annotated[ConnectionPool, Depends(get_pool)]) -> UserRepository:
    return UserRepository(pool)


def get_chat_repo(pool: Annotated[ConnectionPool, Depends(get_pool)]) -> ChatRepository:
    return ChatRepository(pool)


def get_pipeline(
    filings: Annotated[FilingRepository, Depends(get_filing_repo)],
    vectors: Annotated[VectorStore, Depends(get_vector_store)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> IngestionPipeline:
    return IngestionPipeline(filings, vectors, embedder, llm)


def get_chat_service(
    filings: Annotated[FilingRepository, Depends(get_filing_repo)],
    chats: Annotated[ChatRepository, Depends(get_chat_repo)],
    pipeline: Annotated[IngestionPipeline, Depends(get_pipeline)],
    vectors: Annotated[VectorStore, Depends(get_vector_store)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    reranker: Annotated[Reranker, Depends(get_reranker)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    cache: Annotated[dict, Depends(get_retriever_cache)],
) -> ChatService:
    return ChatService(
        filings_repo=filings,
        chat_repo=chats,
        pipeline=pipeline,
        vector_store=vectors,
        embedder=embedder,
        reranker=reranker,
        answer_service=AnswerService(llm),
        retriever_cache=cache,
    )


def current_user(
    users: Annotated[UserRepository, Depends(get_user_repo)],
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Resolve the caller, or None. Never raises - signed out is valid."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    user_id = decode_access_token(
        authorization.removeprefix("Bearer "), settings.jwt_secret, settings.jwt_algorithm
    )
    return users.get(user_id) if user_id is not None else None


def workspace_id(user: Annotated[User | None, Depends(current_user)]) -> int | None:
    """The workspace a request operates in: the user's, or the shared guest one."""
    return user.id if user else None


CurrentUser = Annotated[User | None, Depends(current_user)]
WorkspaceId = Annotated[int | None, Depends(workspace_id)]
Filings = Annotated[FilingRepository, Depends(get_filing_repo)]
Users = Annotated[UserRepository, Depends(get_user_repo)]
Chats = Annotated[ChatRepository, Depends(get_chat_repo)]
Pipeline = Annotated[IngestionPipeline, Depends(get_pipeline)]
Chat = Annotated[ChatService, Depends(get_chat_service)]
LLM = Annotated[LLMClient, Depends(get_llm_client)]
Files = Annotated[FileStore, Depends(get_file_store)]
