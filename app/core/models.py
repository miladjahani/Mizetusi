from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Any
class UserCreate(BaseModel):
    model_config=ConfigDict(extra='ignore')
    username:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.-]+$')
    protocol:str=Field(default='vless',pattern=r'^(vless|trojan|vmess|ss)$')
    limit_gb:Optional[float]=Field(default=None,ge=0)
    expiry_days:Optional[int]=Field(default=None,ge=0)
    limit_req:Optional[int]=Field(default=None,ge=0)
    ip_limit:Optional[int]=Field(default=None,ge=1)
    start_on_first_connect:bool=False
    ips:str=''; fingerprint:str='chrome'; tls:str='on'; port:int=Field(default=443,ge=1,le=65535)
    sni:Optional[str]=None; host:Optional[str]=None; frag_len:str=''; frag_int:str=''
    advanced_frag:Optional[str]=None; cipher_suites:Optional[str]=None; tls_mask:Optional[str]=None
    block_ads:bool=False; block_porn:bool=False; auto_rotate_ip:bool=False
    rotate_time:int=Field(default=5,ge=1); ip_operator:str='all'; ip_count:int=Field(default=5,ge=1); user_proxy:Optional[str]=None
    metadata:dict[str,Any]={}
class TrafficEvent(BaseModel):
    bytes:int=Field(default=0,ge=0); requests:int=Field(default=1,ge=0); ip:Optional[str]=None
