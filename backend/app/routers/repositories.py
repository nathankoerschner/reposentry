"""Repository endpoints."""

import uuid
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.repositories import RepositoryCreate, RepositoryResponse

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


def _parse_github_url(url: str) -> tuple[str, str, str]:
    """Extract host, owner, name from a validated GitHub URL."""
    parsed = urlparse(url)
    host = parsed.hostname or "github.com"
    parts = parsed.path.strip("/").split("/")
    owner = parts[0]
    name = parts[1].removesuffix(".git") if len(parts) > 1 else parts[0]
    return host, owner, name


async def _get_default_branch(owner: str, name: str) -> str | None:
    """Fetch the repository default branch from the GitHub REST API."""
    url = f"https://api.github.com/repos/{owner}/{name}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "zeropath-local-dev",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Repository not found on GitHub")
            response.raise_for_status()
            data = response.json()
            return data.get("default_branch")
    except HTTPException:
        raise
    except Exception:
        return None


@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED)
async def create_repository(
    body: RepositoryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register a new GitHub repository."""
    host, owner, name = _parse_github_url(body.url)

    # Check for duplicate
    existing = (
        db.query(Repository)
        .filter(Repository.user_id == user.id, Repository.owner == owner, Repository.name == name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Repository already registered")

    default_branch = await _get_default_branch(owner, name)

    repo = Repository(
        user_id=user.id,
        url=body.url,
        host=host,
        owner=owner,
        name=name,
        default_branch=default_branch,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


@router.get("", response_model=list[RepositoryResponse])
async def list_repositories(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List repositories for the current user."""
    return db.query(Repository).filter(Repository.user_id == user.id).order_by(Repository.created_at.desc()).all()


@router.get("/{repository_id}", response_model=RepositoryResponse)
async def get_repository(
    repository_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch a single repository."""
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == user.id).first()
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repo


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(
    repository_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a repository and its related scans/findings via ORM cascades."""
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == user.id).first()
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    db.delete(repo)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
