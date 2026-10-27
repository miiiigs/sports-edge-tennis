"""Ensure OddsPapi 403 diagnostics are bounded and secret-safe."""
from datetime import UTC, datetime, timedelta
import pytest
from sports_edge_tennis.live import oddspapi
from sports_edge_tennis.live.transport import TransportError

class Response:
    status_code = 403
    headers = {}
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

@pytest.mark.parametrize(("message","expected"),[
    ("Subscription inactive","SUBSCRIPTION_INACTIVE"),
    ("Restricted sportId.","SPORT_NOT_INCLUDED"),
    ("Restricted tournament.","TOURNAMENT_NOT_INCLUDED"),
    ("Restricted bookmaker(s).","BOOKMAKER_NOT_INCLUDED"),
    ("No bookmakers available for the user.","BOOKMAKER_NOT_INCLUDED"),
    ("No bookmakers with live access found.","LIVE_BOOKMAKER_NOT_INCLUDED"),
    ("Forbidden: your IP address has been blocked","IP_BLOCKED"),
    ("Other restriction","SUBSCRIPTION_RESTRICTED"),
])
def test_403_reports_fixed_code_no_raw_data_or_retry(monkeypatch,message,expected):
    calls=[]
    secret="top-secret-api-key-never-log"
    def denied(url,**kw):
        calls.append(kw)
        return Response({"error":{"code":"RESTRICTED_ACCESS","message":message,"details":secret},"api_key":secret})
    monkeypatch.setattr(oddspapi.requests,"get",denied)
    now=datetime(2026,10,27,tzinfo=UTC)
    with pytest.raises(TransportError) as caught:
        oddspapi.fetch_oddspapi_tennis_fixtures(secret,from_at=now,to_at=now+timedelta(hours=2))
    assert caught.value.kind=="HTTP_403"
    assert caught.value.category==expected
    assert len(calls)==1
    assert secret not in str(caught.value)
    assert secret not in caught.value.category

@pytest.mark.parametrize("payload",[
    {},[],{"error":{"code":"INVALID_API_KEY","message":"secret"}},
    {"error":"denied"},ValueError("unsafe secret"),
])
def test_malformed_or_other_403_is_safe(monkeypatch,payload):
    monkeypatch.setattr(oddspapi.requests,"get",lambda url,**kw:Response(payload))
    now=datetime(2026,10,27,tzinfo=UTC)
    with pytest.raises(TransportError) as caught:
        oddspapi.fetch_oddspapi_tennis_fixtures("hidden",from_at=now,to_at=now+timedelta(hours=2))
    assert caught.value.category=="UNKNOWN_403"
    assert "unsafe" not in str(caught.value)

def test_success_unchanged(monkeypatch):
    class Ok(Response):
        status_code=200
    monkeypatch.setattr(oddspapi.requests,"get",lambda url,**kw:Ok([]))
    now=datetime(2026,10,27,tzinfo=UTC)
    received,rows=oddspapi.fetch_oddspapi_tennis_fixtures(
        "hidden",from_at=now,to_at=now+timedelta(hours=2),clock=lambda:now
    )
    assert received==now and rows==[]
