"""Entire-track models: checkpoints, briefings, reviews, drift, demos."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CheckpointIn(BaseModel):
    tender_id: str = Field(min_length=1)
    run_id: str = ""
    request: str = ""
    decisions: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    git_commit: str = ""
    branch: str = ""


class CheckpointOut(BaseModel):
    id: str
    tender_id: str
    run_id: str
    request: str
    decisions: list[str]
    changed_files: list[str]
    validation: dict
    git_commit: str
    branch: str
    created_at: float
    entire_url: str = ""


class BriefIn(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = 3


class ReviewIn(BaseModel):
    request: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    checkpoint_id: str = ""
