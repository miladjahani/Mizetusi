from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, Any

# A user's protocol selection. It is stored as a comma-separated list because
# one credential (UUID/password) is valid on every inbound: "vless,trojan,vmess,ss"
# means all of those links work for that user. ``all`` is the default, and the
# bare single value older releases wrote keeps meaning the full set.
PROTOCOL_LIST = r'^(all|\*|(vless|vmess|trojan|ss)([,\s+]+(vless|vmess|trojan|ss))*)$'


class UserCreate(BaseModel):
    model_config=ConfigDict(extra='ignore')
    username:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.-]+$')
    protocol:str=Field(default='all',pattern=PROTOCOL_LIST)
    limit_gb:Optional[float]=Field(default=None,ge=0)
    expiry_days:Optional[int]=Field(default=None,ge=0)
    limit_req:Optional[int]=Field(default=None,ge=0)
    ip_limit:Optional[int]=Field(default=None,ge=1)
    # How many configs this user's subscription may contain (empty = every
    # published combination). Stored in ``metadata`` so no schema migration is
    # needed for a value only the generator reads.
    max_configs:Optional[int]=Field(default=None,ge=1,le=500)
    # Which nodes this user's subscription may contain: ``all``, ``multi`` (only
    # the edge locations), ``origin`` (only this server's relay) or a country
    # (``us`` / ``cc:us``). ``app.subscriptions.scope`` normalises the spelling.
    node_scope:Optional[str]=Field(default=None,max_length=120)
    start_on_first_connect:bool=False
    ips:str=''; fingerprint:str='chrome'; tls:str='on'; port:int=Field(default=443,ge=1,le=65535)
    sni:Optional[str]=None; host:Optional[str]=None; frag_len:str=''; frag_int:str=''
    advanced_frag:Optional[str]=None; cipher_suites:Optional[str]=None; tls_mask:Optional[str]=None
    block_ads:bool=False; block_porn:bool=False; auto_rotate_ip:bool=False
    rotate_time:int=Field(default=5,ge=1); ip_operator:str='all'; ip_count:int=Field(default=5,ge=1); user_proxy:Optional[str]=None
    metadata:dict[str,Any]={}

    @field_validator('node_scope', mode='before')
    @classmethod
    def _normalize_scope(cls, value):
        """Accept any scope spelling and store the canonical one."""
        if value in (None, ''):
            return None
        from app.subscriptions.scope import normalize
        return normalize(value)

    @field_validator('protocol', mode='before')
    @classmethod
    def _normalize_protocols(cls, value):
        """Accept a list (the panel's checkboxes) as well as a string."""
        if isinstance(value,(list,tuple,set)):
            selected=[str(item).strip().lower() for item in value if str(item).strip()]
            return ','.join(selected) if selected else 'all'
        return str(value or 'all').strip().lower() or 'all'


class TrafficEvent(BaseModel):
    bytes:int=Field(default=0,ge=0); requests:int=Field(default=1,ge=0); ip:Optional[str]=None
