from pydantic import BaseModel
from typing import Optional


class PluginCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    version: Optional[str] = "1.0"
    author: Optional[str] = "匿名"
    tag_ids: Optional[list[int]] = None
    netdisk_url: Optional[str] = ""
    download_mode: Optional[str] = "direct_only"


class PluginEdit(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    author: Optional[str] = None
    tag_ids: Optional[list[int]] = None
    netdisk_url: Optional[str] = None
    download_mode: Optional[str] = None
