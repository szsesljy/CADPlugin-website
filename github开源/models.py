from pydantic import BaseModel
from typing import Optional


class PluginCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    version: Optional[str] = "1.0"
    author: Optional[str] = "匿名"
    tag_ids: Optional[list[int]] = None


class PluginEdit(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    author: Optional[str] = None
    tag_ids: Optional[list[int]] = None
