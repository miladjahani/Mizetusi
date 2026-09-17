from app.main import _vless_request, _trojan_request
import uuid

def test_vless_parser():
    u=uuid.uuid4(); host=b'example.com'; port=443
    data=b'\x01'+u.bytes+b'\x00'+port.to_bytes(2,'big')+b'\x01'+bytes([2,len(host)])+host+b'hello'
    got=_vless_request(data)
    assert got[0]==str(u) and got[1]=='example.com' and got[2]==443 and got[3]==b'hello'

def test_trojan_parser():
    host=b'example.com'
    data=b'pw\r\n\r\n'+bytes([1,3])+ (443).to_bytes(2,'big') + bytes([len(host)])+host+b'hello'
    got=_trojan_request(data)
    assert got[0]=='pw' and got[1]=='example.com' and got[2]==443 and got[3]==b'hello'
