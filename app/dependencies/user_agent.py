from typing import Annotated

from app.models.model import UserAgentInfo as UserAgentInfoModel
from app.utils import extract_user_agent

from fast_depends import Depends as DIDepends
from fastapi import Depends, Header


def get_user_agent_info(user_agent: str | None = Header(None, include_in_schema=False)) -> UserAgentInfoModel:
    return extract_user_agent(user_agent)


UserAgentInfo = Annotated[UserAgentInfoModel, Depends(get_user_agent_info), DIDepends(get_user_agent_info)]
