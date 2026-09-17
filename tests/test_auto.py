import os
os.environ['ENVIRONMENT']='development'
os.environ['SQLITE_PATH']=':memory:'

def test_monitor_import():
    from app.cloudflare.monitor import expand_networks
    assert expand_networks([])==[]

def test_app_import():
    from app.main import app
    assert app.title.startswith('NEXUS')
