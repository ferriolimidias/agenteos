"""Server-owned tenant/channel rollout gates. Preview never changes these flags."""
from __future__ import annotations
import os
import uuid

CHANNELS = ('whatsapp', 'instagram', 'simulator')


def tenant_suffix(tenant):
    return uuid.UUID(str(tenant)).hex.upper()


def rollout(tenant, channel='whatsapp'):
    try:
        suffix = tenant_suffix(tenant)
    except (ValueError, TypeError, AttributeError):
        return {'requested_mode': 'LEGACY', 'mode': 'LEGACY', 'live_authorized': False, 'authorized_channels': []}
    requested = os.getenv('A07_MODE_' + suffix, 'LEGACY').strip().upper()
    if requested not in ('LEGACY', 'MODULAR'):
        requested = 'LEGACY'
    authorized = os.getenv('A07_LIVE_AUTHORIZED_' + suffix, '').strip().lower() in ('true', '1', 'yes')
    raw = os.getenv('A07_AUTHORIZED_CHANNELS_' + suffix, '')
    channels = sorted({c.strip().lower() for c in raw.split(',') if c.strip().lower() in ('whatsapp', 'instagram')}) if authorized else []
    effective = 'MODULAR' if requested == 'MODULAR' and authorized and channel in channels else 'LEGACY'
    return {'requested_mode': requested, 'mode': effective, 'live_authorized': authorized, 'authorized_channels': channels}
