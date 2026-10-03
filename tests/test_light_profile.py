"""The light profile stretches the panel's background loops, never the data plane.

``NEXUS_LIGHT=1`` exists so a small or metered instance idles quietly: rarer
housekeeping passes and a smaller probe volume. The three properties worth
pinning are the ones the feature promises — without the switch nothing changes,
with it every cadence is stretched and the probe volume is capped, and a value
an admin already set calmer is never tightened back up.
"""
from app.config import Settings, _apply_light


def test_default_profile_is_untouched():
    # ``_env_file=None`` keeps a deployment's own override file out of the test:
    # the property under test is that the shipped profile is a no-op when the
    # switch is off, whatever cadence an operator configured elsewhere.
    s = Settings(_env_file=None)
    assert s.light is False
    before = (s.xray_sync_interval, s.cores_sync_interval, s.telegram_sync_interval,
              s.auto_reset_interval, s.cf_probe_interval, s.cf_probe_limit, s.cf_probe_concurrency)
    after = _apply_light(s)
    assert after is s
    assert (after.xray_sync_interval, after.cores_sync_interval, after.telegram_sync_interval,
            after.auto_reset_interval, after.cf_probe_interval, after.cf_probe_limit,
            after.cf_probe_concurrency) == before


def test_light_profile_stretches_every_loop():
    s = _apply_light(Settings(light=True))
    assert s.light is True
    assert s.xray_sync_interval >= 30
    assert s.cores_sync_interval >= 60
    assert s.telegram_sync_interval >= 60
    assert s.auto_reset_interval >= 300
    assert s.cf_probe_interval >= 1800
    # The probe volume is capped, not stretched: fewer addresses, fewer in flight.
    assert s.cf_probe_limit <= 32
    assert s.cf_probe_concurrency <= 4


def test_light_profile_never_tightens_explicit_values():
    s = _apply_light(Settings(
        light=True,
        xray_sync_interval=120,
        cores_sync_interval=180,
        telegram_sync_interval=180,
        auto_reset_interval=900,
        cf_probe_interval=7200,
        cf_probe_limit=16,
        cf_probe_concurrency=2,
    ))
    assert s.xray_sync_interval == 120
    assert s.cores_sync_interval == 180
    assert s.telegram_sync_interval == 180
    assert s.auto_reset_interval == 900
    assert s.cf_probe_interval == 7200
    assert s.cf_probe_limit == 16
    assert s.cf_probe_concurrency == 2
