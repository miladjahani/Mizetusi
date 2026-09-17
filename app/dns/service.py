import httpx
async def doh(name,endpoint='https://cloudflare-dns.com/dns-query'):
    async with httpx.AsyncClient(timeout=8) as c:
        r=await c.get(endpoint,params={'name':name,'type':'A'},headers={'accept':'application/dns-json'}); r.raise_for_status(); return r.json()
