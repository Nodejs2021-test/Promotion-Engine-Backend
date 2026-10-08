"""API key request model."""

from pydantic import BaseModel, Field


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="e.g. 'NetSuite production'")
