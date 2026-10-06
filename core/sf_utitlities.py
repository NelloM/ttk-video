"""Notifica Salesforce: OAuth 2.0 Client Credentials + POST su Apex REST (/services/apexrest/video/ready)."""
import os
import time

import requests

_tok = {"value": None, "instance": None, "exp": 0.0}


def _login() -> None:
    if not all(os.getenv(k) for k in ("SF_MY_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET")):
        raise RuntimeError("Configurazione Salesforce mancante nel .env (SF_MY_DOMAIN, SF_CLIENT_ID, SF_CLIENT_SECRET)")
    r = requests.post(
        f"{os.environ['SF_MY_DOMAIN'].rstrip('/')}/services/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["SF_CLIENT_ID"],
            "client_secret": os.environ["SF_CLIENT_SECRET"],
        },
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Login Salesforce {r.status_code}: {r.text[:300]}")
    d = r.json()
    _tok.update(value=d["access_token"], instance=d["instance_url"], exp=time.time() + 25 * 60)


def notify_salesforce(payload: dict) -> dict:
    print('INVIATO A SF:', payload)
    # for attempt in (1, 2):
    #     if time.time() >= _tok["exp"]:
    #         _login()
    #     r = requests.post(
    #         f"{_tok['instance']}/services/apexrest/video/ready",
    #         json=payload,
    #         headers={"Authorization": f"Bearer {_tok['value']}"},
    #         timeout=30,
    #     )
    #     if r.status_code == 401 and attempt == 1:  # token scaduto: rifai il login
    #         _tok["exp"] = 0
    #         continue
    #     if not r.ok:
    #         raise RuntimeError(f"Salesforce {r.status_code}: {r.text[:300]}")
    #     return r.json()