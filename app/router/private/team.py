import hashlib
from typing import Annotated

from app.database.team import Team, TeamMember, TeamRequest, TeamResp
from app.database.user import BASE_INCLUDES, User, UserResp
from app.dependencies.database import Database, Redis
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.models.notification import (
    TeamApplicationAccept,
    TeamApplicationReject,
    TeamApplicationStore,
)
from app.models.score import GameMode
from app.router.notification import server
from app.service.ranking_cache_service import get_ranking_cache_service
from app.utils import check_image, utcnow

from .router import router

from fastapi import File, Form, HTTPException, Path, Query, Request
from pydantic import BaseModel
from sqlmodel import col, exists, select


@router.post("/team", name="创建战队", response_model=Team, tags=["战队", "g0v0 API"])
async def create_team(
    session: Database,
    storage: StorageService,
    current_user: ClientUser,
    flag: Annotated[bytes, File(..., description="战队图标文件")],
    cover: Annotated[bytes, File(..., description="战队头图文件")],
    name: Annotated[str, Form(max_length=100, description="战队名称")],
    short_name: Annotated[str, Form(max_length=10, description="战队缩写")],
    redis: Redis,
    playmode: Annotated[GameMode, Form(description="战队游戏模式")] = GameMode.OSU,
    description: Annotated[str | None, Form(description="战队简介")] = None,
    website: Annotated[str | None, Form(description="战队网址")] = None,
):
    """创建战队。

    flag 限制 240x120, 2MB; cover 限制 3000x2000, 10MB
    支持的图片格式: PNG、JPEG、GIF
    """
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    user_id = current_user.id
    if (await current_user.awaitable_attrs.team_membership) is not None:
        raise HTTPException(status_code=403, detail="You are already in a team")

    is_existed = (await session.exec(select(exists()).where(Team.name == name))).first()
    if is_existed:
        raise HTTPException(status_code=409, detail="Name already exists")
    is_existed = (await session.exec(select(exists()).where(Team.short_name == short_name))).first()
    if is_existed:
        raise HTTPException(status_code=409, detail="Short name already exists")

    flag_format = check_image(flag, 2 * 1024 * 1024, 240, 120)
    cover_format = check_image(cover, 10 * 1024 * 1024, 3000, 2000)

    if website and not (website.startswith("http://") or website.startswith("https://")):
        website = "https://" + website

    now = utcnow()
    team = Team(
        name=name,
        short_name=short_name,
        leader_id=user_id,
        created_at=now,
        playmode=playmode,
        description=description,
        website=website,
    )
    session.add(team)
    await session.commit()
    await session.refresh(team)

    filehash = hashlib.sha256(flag).hexdigest()
    storage_path = f"team_flag/{team.id}_{filehash}.png"
    if not await storage.is_exists(storage_path):
        await storage.write_file(storage_path, flag, f"image/{flag_format}")
    team.flag_url = await storage.get_file_url(storage_path)

    filehash = hashlib.sha256(cover).hexdigest()
    storage_path = f"team_cover/{team.id}_{filehash}.png"
    if not await storage.is_exists(storage_path):
        await storage.write_file(storage_path, cover, f"image/{cover_format}")
    team.cover_url = await storage.get_file_url(storage_path)

    team_member = TeamMember(user_id=user_id, team_id=team.id, joined_at=now)
    session.add(team_member)

    await session.commit()
    await session.refresh(team)

    cache_service = get_ranking_cache_service(redis)
    await cache_service.invalidate_team_cache()
    return team


@router.patch("/team/{team_id}", name="修改战队", response_model=Team, tags=["战队", "g0v0 API"])
async def update_team(
    team_id: int,
    session: Database,
    storage: StorageService,
    current_user: ClientUser,
    flag: Annotated[bytes | None, File(description="战队图标文件")] = None,
    cover: Annotated[bytes | None, File(description="战队头图文件")] = None,
    name: Annotated[str | None, Form(max_length=100, description="战队名称")] = None,
    short_name: Annotated[str | None, Form(max_length=10, description="战队缩写")] = None,
    leader_id: Annotated[int | None, Form(description="战队队长 ID")] = None,
    playmode: Annotated[GameMode, Form(description="战队游戏模式")] = GameMode.OSU,
    description: Annotated[str | None, Form(description="战队简介")] = None,
    website: Annotated[str | None, Form(description="战队网址")] = None,
):
    """修改战队。

    flag 限制 240x120, 2MB; cover 限制 3000x2000, 10MB
    支持的图片格式: PNG、JPEG、GIF
    """
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    team = await session.get(Team, team_id)
    user_id = current_user.id
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.leader_id != user_id:
        raise HTTPException(status_code=403, detail="You are not the team leader")

    if name is not None:
        if (await session.exec(select(exists()).where(Team.name == name))).first():
            raise HTTPException(status_code=409, detail="Name already exists")
        else:
            team.name = name
    if short_name is not None:
        if (await session.exec(select(exists()).where(Team.short_name == short_name))).first():
            raise HTTPException(status_code=409, detail="Short name already exists")
        else:
            team.short_name = short_name

    team.playmode = playmode or team.playmode
    team.description = description
    if website is not None:
        if website and not (website.startswith("http://") or website.startswith("https://")):
            website = "https://" + website
        team.website = website

    if flag:
        format_ = check_image(flag, 2 * 1024 * 1024, 240, 120)

        if old_flag := team.flag_url:
            path = storage.get_file_name_by_url(old_flag)
            if path:
                await storage.delete_file(path)
        filehash = hashlib.sha256(flag).hexdigest()
        storage_path = f"team_flag/{team.id}_{filehash}.png"
        if not await storage.is_exists(storage_path):
            await storage.write_file(storage_path, flag, f"image/{format_}")
        team.flag_url = await storage.get_file_url(storage_path)
    if cover:
        format_ = check_image(cover, 10 * 1024 * 1024, 3000, 2000)

        if old_cover := team.cover_url:
            path = storage.get_file_name_by_url(old_cover)
            if path:
                await storage.delete_file(path)
        filehash = hashlib.sha256(cover).hexdigest()
        storage_path = f"team_cover/{team.id}_{filehash}.png"
        if not await storage.is_exists(storage_path):
            await storage.write_file(storage_path, cover, f"image/{format_}")
        team.cover_url = await storage.get_file_url(storage_path)

    if leader_id is not None:
        if not (await session.exec(select(exists()).where(User.id == leader_id))).first():
            raise HTTPException(status_code=404, detail="Leader not found")
        if not (
            await session.exec(select(TeamMember).where(TeamMember.user_id == leader_id, TeamMember.team_id == team.id))
        ).first():
            raise HTTPException(status_code=404, detail="Leader is not a member of the team")
        team.leader_id = leader_id

    await session.commit()
    await session.refresh(team)
    return team


@router.delete("/team/{team_id}", name="删除战队", status_code=204, tags=["战队", "g0v0 API"])
async def delete_team(
    session: Database,
    team_id: Annotated[int, Path(..., description="战队 ID")],
    current_user: ClientUser,
    redis: Redis,
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if team.leader_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not the team leader")

    team_members = await session.exec(select(TeamMember).where(TeamMember.team_id == team_id))
    for member in team_members:
        await session.delete(member)

    await session.delete(team)
    await session.commit()

    cache_service = get_ranking_cache_service(redis)
    await cache_service.invalidate_team_cache()


class TeamQueryResp(BaseModel):
    team: TeamResp
    members: list[UserResp]


@router.get("/team/{team_id}", name="查询战队", response_model=TeamQueryResp, tags=["战队", "g0v0 API"])
async def get_team(
    session: Database,
    team_id: Annotated[int, Path(..., description="战队 ID")],
    gamemode: Annotated[GameMode | None, Query(description="游戏模式")] = None,
):
    members = (
        await session.exec(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                ~User.is_restricted_query(col(TeamMember.user_id)),
            )
        )
    ).all()
    return TeamQueryResp(
        team=await TeamResp.from_db(members[0].team, session, gamemode),
        members=[await UserResp.from_db(m.user, session, include=BASE_INCLUDES) for m in members],
    )


@router.post("/team/{team_id}/request", name="请求加入战队", status_code=204, tags=["战队", "g0v0 API"])
async def request_join_team(
    session: Database,
    team_id: Annotated[int, Path(..., description="战队 ID")],
    current_user: ClientUser,
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if (await current_user.awaitable_attrs.team_membership) is not None:
        raise HTTPException(status_code=403, detail="You are already in a team")

    if (
        await session.exec(
            select(exists()).where(TeamRequest.team_id == team_id, TeamRequest.user_id == current_user.id)
        )
    ).first():
        raise HTTPException(status_code=409, detail="Join request already exists")
    team_request = TeamRequest(user_id=current_user.id, team_id=team_id, requested_at=utcnow())
    session.add(team_request)
    await session.commit()
    await session.refresh(team_request)
    await server.new_private_notification(TeamApplicationStore.init(team_request))


@router.post("/team/{team_id}/{user_id}/request", name="接受加入请求", status_code=204, tags=["战队", "g0v0 API"])
@router.delete("/team/{team_id}/{user_id}/request", name="拒绝加入请求", status_code=204, tags=["战队", "g0v0 API"])
async def handle_request(
    req: Request,
    session: Database,
    team_id: Annotated[int, Path(..., description="战队 ID")],
    user_id: Annotated[int, Path(..., description="用户 ID")],
    current_user: ClientUser,
    redis: Redis,
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if team.leader_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not the team leader")

    team_request = (
        await session.exec(select(TeamRequest).where(TeamRequest.team_id == team_id, TeamRequest.user_id == user_id))
    ).first()
    if not team_request:
        raise HTTPException(status_code=404, detail="Join request not found")

    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if req.method == "POST":
        if (await session.exec(select(exists()).where(TeamMember.user_id == user_id))).first():
            raise HTTPException(status_code=409, detail="User is already a member of the team")

        session.add(TeamMember(user_id=user_id, team_id=team_id, joined_at=utcnow()))

        await server.new_private_notification(TeamApplicationAccept.init(team_request))

        cache_service = get_ranking_cache_service(redis)
        await cache_service.invalidate_team_cache()
    else:
        await server.new_private_notification(TeamApplicationReject.init(team_request))
    await session.delete(team_request)
    await session.commit()


@router.delete("/team/{team_id}/{user_id}", name="踢出成员 / 退出战队", status_code=204, tags=["战队", "g0v0 API"])
async def kick_member(
    session: Database,
    team_id: Annotated[int, Path(..., description="战队 ID")],
    user_id: Annotated[int, Path(..., description="用户 ID")],
    current_user: ClientUser,
    redis: Redis,
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if team.leader_id != current_user.id and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not the team leader")

    team_member = (
        await session.exec(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id))
    ).first()
    if not team_member:
        raise HTTPException(status_code=404, detail="User is not a member of the team")

    if team.leader_id == current_user.id:
        raise HTTPException(status_code=403, detail="You cannot leave because you are the team leader")

    await session.delete(team_member)
    await session.commit()

    cache_service = get_ranking_cache_service(redis)
    await cache_service.invalidate_team_cache()
