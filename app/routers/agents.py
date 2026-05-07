"""
智能体管理路由：CRUD + 用途分配。所有接口需要 admin 权限。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_admin
from ..database import (
    delete_agent,
    encrypt_api_key,
    get_agent_by_id,
    get_all_agents,
    get_all_assignments,
    insert_agent,
    set_assignment,
    update_agent,
)

router = APIRouter(tags=["agents"])


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _make_preview(api_key: str) -> str:
    if len(api_key) <= 8:
        return api_key[:2] + "****"
    return api_key[:4] + "****" + api_key[-4:]


def _agent_to_dict(a: dict) -> dict:
    return {
        "id": a["id"],
        "name": a["name"],
        "base_url": a["base_url"],
        "api_key_preview": a.get("api_key_preview", ""),
        "model": a["model"],
        "version": a.get("version"),
        "auth_type": a.get("auth_type", "x_api_key"),
        "is_active": bool(a.get("is_active")),
        "created_at": _format_dt(a.get("created_at")),
        "updated_at": _format_dt(a.get("updated_at")),
    }


class CreateAgentBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1, max_length=100)
    version: str = Field(default="2023-06-01", max_length=50)
    auth_type: Literal["x_api_key", "bearer"] = Field(default="x_api_key")


class UpdateAgentBody(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    version: str | None = None
    is_active: bool | None = None
    auth_type: Literal["x_api_key", "bearer"] | None = None


class AssignmentBody(BaseModel):
    seed_evolution: int | None = None
    keyword_report: int | None = None


def _assignments_response() -> dict:
    """用途分配列表（不含鉴权，供 GET 与 PUT 复用）。"""
    rows = get_all_assignments()
    result: dict = {}
    for r in rows:
        result[r["usage"]] = {
            "agent_id": r["agent_id"],
            "agent_name": r.get("agent_name"),
            "model": r.get("model"),
            "is_active": bool(r.get("is_active")),
            "updated_at": _format_dt(r.get("updated_at")),
        }
    return result


@router.get("/agents")
def list_agents(
    _: Annotated[dict, Depends(require_admin)],
) -> dict:
    agents = get_all_agents()
    return {
        "total": len(agents),
        "items": [_agent_to_dict(a) for a in agents],
    }


@router.post("/agents")
def create_agent_endpoint(
    body: CreateAgentBody,
    _: Annotated[dict, Depends(require_admin)],
) -> dict:
    try:
        api_key_enc = encrypt_api_key(body.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = {
        "name": body.name,
        "base_url": body.base_url,
        "api_key_enc": api_key_enc,
        "api_key_preview": _make_preview(body.api_key),
        "model": body.model,
        "version": body.version,
        "auth_type": body.auth_type,
    }
    try:
        new_id = insert_agent(data)
    except pymysql.err.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="数据库服务暂时不可用",
        ) from exc
    except pymysql.err.IntegrityError as exc:
        raise HTTPException(status_code=400, detail="数据写入失败") from exc

    agent = get_agent_by_id(new_id)
    return _agent_to_dict(agent) if agent else {"id": new_id}


# 须写在 PUT /agents/{agent_id} 之前，否则路径 assignments 会被当成 agent_id 整型转换失败 → 422
@router.get("/agents/assignments")
def get_assignments_endpoint(
    _: Annotated[dict, Depends(require_admin)],
) -> dict:
    return _assignments_response()


@router.put("/agents/assignments")
def update_assignments_endpoint(
    body: AssignmentBody,
    _: Annotated[dict, Depends(require_admin)],
) -> dict:
    if body.seed_evolution is not None:
        agent = get_agent_by_id(body.seed_evolution)
        if not agent:
            raise HTTPException(status_code=400, detail="seed_evolution 指向的智能体不存在")
        set_assignment("seed_evolution", body.seed_evolution)
    if body.keyword_report is not None:
        agent = get_agent_by_id(body.keyword_report)
        if not agent:
            raise HTTPException(status_code=400, detail="keyword_report 指向的智能体不存在")
        set_assignment("keyword_report", body.keyword_report)
    return _assignments_response()


@router.put("/agents/{agent_id}")
def update_agent_endpoint(
    agent_id: int,
    body: UpdateAgentBody,
    _: Annotated[dict, Depends(require_admin)],
) -> dict:
    existing = get_agent_by_id(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="智能体不存在")

    data: dict = {}
    if body.name is not None:
        data["name"] = body.name
    if body.base_url is not None:
        data["base_url"] = body.base_url
    if body.model is not None:
        data["model"] = body.model
    if body.version is not None:
        data["version"] = body.version
    if body.is_active is not None:
        data["is_active"] = body.is_active
    if body.auth_type is not None:
        data["auth_type"] = body.auth_type
    if body.api_key is not None and body.api_key.strip():
        try:
            data["api_key_enc"] = encrypt_api_key(body.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        data["api_key_preview"] = _make_preview(body.api_key)

    try:
        update_agent(agent_id, data)
    except pymysql.err.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="数据库服务暂时不可用",
        ) from exc
    updated = get_agent_by_id(agent_id)
    return _agent_to_dict(updated) if updated else {"id": agent_id}


@router.delete("/agents/{agent_id}")
def delete_agent_endpoint(
    agent_id: int,
    _: Annotated[dict, Depends(require_admin)],
) -> dict:
    existing = get_agent_by_id(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="智能体不存在")
    try:
        delete_agent(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
