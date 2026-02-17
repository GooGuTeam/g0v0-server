from typing import Annotated

from app.helpers import extract_user_agent
from app.models.model import UserAgentInfo as UserAgentInfoModel

from fast_depends import Depends as FastDepends
from fastapi import Depends, Header


def get_user_agent_info(user_agent: str | None = Header(None, include_in_schema=False)) -> UserAgentInfoModel:
    return extract_user_agent(user_agent)


UserAgentInfo = Annotated[UserAgentInfoModel, Depends(get_user_agent_info), FastDepends(get_user_agent_info)]
