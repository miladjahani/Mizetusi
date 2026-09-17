import asyncio,time
async def tcp_probe(host,port,timeout=3):
    t=time.perf_counter()
    try:
        r,w=await asyncio.wait_for(asyncio.open_connection(host,port),timeout); w.close(); await w.wait_closed(); return round((time.perf_counter()-t)*1000,1)
    except Exception:return None
async def scan(items,concurrency=50):
    sem=asyncio.Semaphore(concurrency)
    async def one(x):
        async with sem:return x,await tcp_probe(x[0],x[1])
    return await asyncio.gather(*(one(x) for x in items))
