import base64
import hashlib
import io
import json
import socket
import threading
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from szurubooru.func import bluesky_network as network


DID = "did:plc:abcdefghijklmnopqrstuvwx"
HANDLE = "author.bsky.social"
RKEY = "record_key"
PDS = "https://pds.example.com"
ORIGINAL = b"the exact original file\x00\xff"
CID = "b" + base64.b32encode(
    b"\x01\x55\x12\x20" + hashlib.sha256(ORIGINAL).digest()
).decode("ascii").lower().rstrip("=")
BLOB = (
    PDS
    + "/xrpc/com.atproto.sync.getBlob?"
    + urlencode({"did": DID, "cid": CID})
)


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    monkeypatch.setattr(network, "_REQUEST_LOCK", threading.Lock())
    monkeypatch.setattr(network, "_LAST_REQUEST", 0.0)
    monkeypatch.setattr(network.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(network.time, "monotonic", lambda: 1000.0)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Unexpected external network request")

    monkeypatch.setattr(network.socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(network.socket, "socket", unexpected)


def document(**updates):
    result = {
        "id": DID,
        "alsoKnownAs": ["at://" + HANDLE],
        "service": [
            {
                "id": "#atproto_pds",
                "type": "AtprotoPersonalDataServer",
                "serviceEndpoint": PDS,
            }
        ],
    }
    result.update(updates)
    return result


def payload(**updates):
    post = {
        "uri": "at://%s/app.bsky.feed.post/%s" % (DID, RKEY),
        "author": {"did": DID, "handle": HANDLE},
    }
    post.update(updates)
    return {"posts": [post]}


@pytest.mark.parametrize(
    "url",
    [
        "https://bsky.app/profile/author.bsky.social/post/record_key",
        "http://www.bsky.app/profile/Author.Bsky.Social/post/record_key?ref=discard#comments",
        "https://fxbsky.app/profile/author.bsky.social/post/record_key",
        "https://www.fxbsky.app/profile/author.bsky.social/post/record_key/?token=discard",
    ],
)
def test_supported_links_normalize_without_query_or_proxy_service(url):
    assert network.parse_post_url(url) == (
        HANDLE,
        RKEY,
        "https://bsky.app/profile/%s/post/%s" % (HANDLE, RKEY),
        None,
    )


def test_did_links_and_selected_media_keep_stable_identity():
    result = network.parse_post_url(
        "https://bsky.app/profile/"
        + DID
        + "/post/record_key?discard=1#media-2"
    )
    assert result == (
        DID,
        RKEY,
        "https://bsky.app/profile/" + DID + "/post/record_key#media-2",
        2,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://bsky.app.evil.test/profile/author.bsky.social/post/record_key",
        "https://user:secret@bsky.app/profile/author.bsky.social/post/record_key",
        "https://bsky.app:443/profile/author.bsky.social/post/record_key",
        "file:///profile/author.bsky.social/post/record_key",
        "https://bsky.app/profile/localhost/post/record_key",
        "https://bsky.app/profile/did:web:localhost/post/record_key",
        "https://bsky.app/profile/did:web:example.com%3A443/post/record_key",
        "https://bsky.app/profile/author.bsky.social/post/..",
        "https://bsky.app/profile/author.bsky.social/post/a+b",
        "https://bsky.app/profile/author.bsky.social/post/record_key#media-0",
        "https://bsky.app/profile/author.bsky.social/post/record_key#media-21",
        "https://bsky.app/profile/author.bsky.social/post/record_key/other",
    ],
)
def test_bad_post_links_fail_before_any_network(url):
    with pytest.raises(ValueError):
        network.parse_post_url(url)


@pytest.mark.parametrize("key", ["self", "pre:fix", "~1.2-3_", "x" * 512])
def test_record_keys_follow_protocol_syntax_without_assuming_tid(key):
    assert network.validate_rkey(key) == key


@pytest.mark.parametrize(
    "cid",
    [None, "bnotacid", CID.upper(), CID + "=", "z" + CID[1:], "b" + "a" * 58],
)
def test_invalid_or_unsupported_blob_cids_fail(cid):
    with pytest.raises(ValueError):
        network.validate_cid(cid)


def test_cid_identifies_the_exact_original_bytes():
    assert network.validate_cid(CID) == hashlib.sha256(ORIGINAL).digest()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://pds.example.com",
        "https://user:secret@pds.example.com",
        "https://pds.example.com:444",
        "https://pds.example.com/prefix",
        "https://pds.example.com?token=secret",
        "https://127.0.0.1",
        "https://[::1]",
        "https://pds.localhost",
        "https://pds.example.com#fragment",
    ],
)
def test_pds_origins_reject_unsafe_or_non_origin_urls(endpoint):
    with pytest.raises(ValueError):
        network.validate_pds_url(endpoint)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.0.1",
        "172.16.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::",
        "2001:db8::1",
    ],
)
def test_dns_must_not_resolve_to_local_private_or_reserved_addresses(
    monkeypatch, address
):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (family, socket.SOCK_STREAM, 6, "", (address, 443))
        ],
    )
    with pytest.raises(network.BlueskyError):
        network._addresses("pds.example.com")


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 443),
            ),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(network.BlueskyError):
        network._addresses("pds.example.com")


def test_tls_connects_to_pinned_address_but_verifies_original_hostname(
    monkeypatch,
):
    raw = MagicMock()
    context = MagicMock()
    monkeypatch.setattr(network.socket, "socket", lambda *_args: raw)
    monkeypatch.setattr(network.ssl, "create_default_context", lambda: context)
    connection = network._PinnedHTTPSConnection(
        "pds.example.com", "93.184.216.34", socket.AF_INET, 15
    )
    connection.connect()
    raw.connect.assert_called_once_with(("93.184.216.34", 443))
    context.wrap_socket.assert_called_once_with(
        raw, server_hostname="pds.example.com"
    )
    assert connection.sock == context.wrap_socket.return_value


def response_transport(monkeypatch, body=b"{}", status=200, headers=None):
    response = MagicMock()
    response.status = status
    response.read.side_effect = io.BytesIO(body).read
    response.getheader.side_effect = lambda key: (headers or {}).get(key)
    connection = MagicMock()
    connection.getresponse.return_value = response
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(
        network,
        "_addresses",
        lambda _host: [(socket.AF_INET, "93.184.216.34")],
    )
    monkeypatch.setattr(network, "_PinnedHTTPSConnection", factory)
    return factory, connection


def test_transport_uses_pinned_connection_and_never_environment_proxy_or_credentials(
    monkeypatch, config_injector
):
    config_injector(
        {
            "post_import": {
                "bluesky": {"username": "private", "api_key": "secret"},
                "furaffinity": {"a": "private-cookie", "b": "other-cookie"},
            }
        }
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    factory, connection = response_transport(monkeypatch, body=b"original")
    assert (
        network._fetch_bytes(
            PDS + "/xrpc/read?value=1", 100, "WildpawsTest/1.0"
        )
        == b"original"
    )
    assert factory.call_args.args[:3] == (
        "pds.example.com",
        "93.184.216.34",
        socket.AF_INET,
    )
    args, kwargs = connection.request.call_args
    assert args == ("GET", "/xrpc/read?value=1")
    assert kwargs["headers"] == {
        "Host": "pds.example.com",
        "User-Agent": "WildpawsTest/1.0",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    connection.close.assert_called_once()


def test_contended_request_waits_then_completes_after_preview_releases_limiter(
    monkeypatch,
):
    held = threading.Lock()
    held.acquire()
    attempted = threading.Event()
    completed = threading.Event()
    acquire_calls = []
    result = []
    failure = []

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            acquire_calls.append((args, kwargs))
            attempted.set()
            return held.acquire(*args, **kwargs)

        def release(self):
            held.release()

    monkeypatch.setattr(network, "_REQUEST_LOCK", ObservedLock())
    _factory, connection = response_transport(monkeypatch, ORIGINAL)

    def upload():
        try:
            result.append(network._fetch_bytes(PDS + "/original", 1000))
        except Exception as error:
            failure.append(error)
        finally:
            completed.set()

    worker = threading.Thread(target=upload)
    worker.start()
    try:
        assert attempted.wait(timeout=1)
        assert acquire_calls == [((), {"timeout": network._TIMEOUT})]
        assert not completed.is_set()
    finally:
        held.release()
        worker.join(timeout=1)
    assert completed.is_set()
    assert failure == []
    assert result == [ORIGINAL]
    connection.request.assert_called_once()
    assert not held.locked()


def test_request_queue_timeout_is_safe_and_does_not_start_network_io(
    monkeypatch,
):
    limiter = MagicMock()
    limiter.acquire.return_value = False
    monkeypatch.setattr(network, "_REQUEST_LOCK", limiter)
    factory, connection = response_transport(monkeypatch)
    with pytest.raises(network.BlueskyError, match="in progress"):
        network._fetch_bytes(PDS + "/original", 1000)
    limiter.acquire.assert_called_once_with(timeout=network._TIMEOUT)
    limiter.release.assert_not_called()
    factory.assert_not_called()
    connection.request.assert_not_called()


@pytest.mark.parametrize(
    "status", [301, 302, 307, 308, 400, 401, 403, 404, 429, 503]
)
def test_remote_error_and_redirect_bodies_are_never_returned(
    monkeypatch, status
):
    _factory, connection = response_transport(
        monkeypatch,
        b"PRIVATE_PROVIDER_ERROR",
        status,
        {"Location": "http://127.0.0.1/secret"},
    )
    with pytest.raises(network.BlueskyError) as result:
        network._fetch_bytes(PDS + "/read", 100)
    assert "PRIVATE_PROVIDER_ERROR" not in str(result.value)
    assert "127.0.0.1" not in str(result.value)
    connection.request.assert_called_once()


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "500"}])
def test_size_limit_is_enforced_even_without_content_length(
    monkeypatch, headers
):
    response_transport(monkeypatch, b"x" * 101, headers=headers)
    with pytest.raises(network.BlueskyError, match="size"):
        network._fetch_bytes(PDS + "/read", 100)


@pytest.mark.parametrize("body", [b"not JSON", b"[]", b"\xff"])
def test_invalid_json_is_a_safe_metadata_error(monkeypatch, body):
    monkeypatch.setattr(network, "_fetch_bytes", lambda *_args: body)
    with pytest.raises(network.BlueskyError, match="valid metadata"):
        network._json(PDS + "/read")


def test_handle_metadata_reads_only_fixed_public_api_and_matching_did_document(
    monkeypatch,
):
    calls = []
    post = payload()

    def read(url, user_agent=None):
        calls.append(url)
        if "resolveHandle" in url:
            return {"did": DID}
        if "getPosts" in url:
            return post
        return document()

    monkeypatch.setattr(network, "_json", read)
    monkeypatch.setattr(
        network,
        "_addresses",
        lambda _host: [(socket.AF_INET, "93.184.216.34")],
    )
    assert network.fetch_metadata(HANDLE.upper(), RKEY) == (post, DID, PDS)
    assert len(calls) == 3
    assert (
        calls[0]
        == "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle="
        + HANDLE
    )
    assert urlsplit(calls[1]).hostname == "public.api.bsky.app"
    assert parse_qs(urlsplit(calls[1]).query) == {
        "uris": ["at://%s/app.bsky.feed.post/%s" % (DID, RKEY)]
    }
    assert calls[2] == "https://plc.directory/" + DID


def test_unavailable_appview_post_never_falls_back_to_pds_record(monkeypatch):
    read = MagicMock(return_value={"posts": []})
    monkeypatch.setattr(network, "_json", read)
    with pytest.raises(network.BlueskyError, match="unavailable"):
        network.fetch_metadata(DID, RKEY)
    assert read.call_count == 1
    assert "getPosts" in read.call_args.args[0]


@pytest.mark.parametrize(
    "data",
    [
        payload(uri="at://wrong/app.bsky.feed.post/x"),
        payload(author={"did": "did:web:wrong.example"}),
        {"posts": [None]},
        {"posts": [{}, {}]},
    ],
)
def test_metadata_does_not_accept_a_different_or_malformed_post(
    monkeypatch, data
):
    read = MagicMock(return_value=data)
    monkeypatch.setattr(network, "_json", read)
    with pytest.raises(network.BlueskyError):
        network.fetch_metadata(DID, RKEY)
    assert read.call_count == 1


def test_handle_claim_must_confirm_resolved_identity(monkeypatch):
    read = MagicMock(
        side_effect=[
            {"did": DID},
            payload(),
            document(alsoKnownAs=["at://other.example.com"]),
        ]
    )
    monkeypatch.setattr(network, "_json", read)
    with pytest.raises(network.BlueskyError, match="no longer matches"):
        network.fetch_metadata(HANDLE, RKEY)


def test_did_web_identity_is_resolved_without_a_path_or_port(monkeypatch):
    did = "did:web:identity.example.com"
    read = MagicMock(return_value=document(id=did))
    monkeypatch.setattr(network, "_json", read)
    monkeypatch.setattr(
        network,
        "_addresses",
        lambda _host: [(socket.AF_INET, "93.184.216.34")],
    )
    assert network.resolve_pds(did) == PDS
    read.assert_called_once_with(
        "https://identity.example.com/.well-known/did.json", None
    )


def test_data_server_private_dns_is_rejected_before_returning_a_draft_url(
    monkeypatch,
):
    monkeypatch.setattr(network, "_json", lambda *_args: document())
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(network.BlueskyError, match="non-public"):
        network.resolve_pds(DID)


def test_did_document_id_and_first_matching_service_are_authoritative(
    monkeypatch,
):
    read = MagicMock(return_value=document(id="did:web:wrong.example.com"))
    monkeypatch.setattr(network, "_json", read)
    with pytest.raises(network.BlueskyError, match="mismatched"):
        network.resolve_pds(DID)
    first_bad = {
        "id": DID + "#atproto_pds",
        "type": "AtprotoPersonalDataServer",
        "serviceEndpoint": "http://pds.example.com",
    }
    read.return_value = document(service=[first_bad] + document()["service"])
    with pytest.raises(network.BlueskyError):
        network.resolve_pds(DID)


def test_blob_download_preserves_original_and_verifies_its_cid(monkeypatch):
    resolve = MagicMock(return_value=PDS)
    fetch = MagicMock(return_value=ORIGINAL)
    monkeypatch.setattr(network, "resolve_pds", resolve)
    monkeypatch.setattr(network, "_fetch_bytes", fetch)
    assert network.download_blob(BLOB, 1000, "WildpawsTest/1.0") == ORIGINAL
    resolve.assert_called_once_with(DID, "WildpawsTest/1.0")
    fetch.assert_called_once_with(BLOB, 1000, "WildpawsTest/1.0")


def test_blob_download_checks_current_pds_before_fetching(monkeypatch):
    monkeypatch.setattr(
        network, "resolve_pds", lambda *_args: "https://other.example.com"
    )
    fetch = MagicMock()
    monkeypatch.setattr(network, "_fetch_bytes", fetch)
    with pytest.raises(network.BlueskyError, match="no longer matches"):
        network.download_blob(BLOB, 1000)
    fetch.assert_not_called()


def test_changed_or_transformed_blob_bytes_are_rejected(monkeypatch):
    monkeypatch.setattr(network, "resolve_pds", lambda *_args: PDS)
    monkeypatch.setattr(
        network, "_fetch_bytes", lambda *_args: b"resized or changed"
    )
    with pytest.raises(network.BlueskyError, match="content ID"):
        network.download_blob(BLOB, 1000)


@pytest.mark.parametrize(
    "url",
    [
        BLOB + "&token=secret",
        BLOB + "&cid=" + CID,
        BLOB + "#secret",
        BLOB.replace("https:", "http:"),
        BLOB.replace("pds.example.com", "user:secret@pds.example.com"),
        BLOB.replace("pds.example.com", "127.0.0.1"),
        BLOB.replace(CID, "bad-cid"),
        BLOB.replace("getBlob?", "getBlob/?"),
    ],
)
def test_malformed_blob_endpoints_are_intercepted_but_not_downloaded(
    monkeypatch, url
):
    assert network.is_blob_url(url)
    resolve = MagicMock()
    fetch = MagicMock()
    monkeypatch.setattr(network, "resolve_pds", resolve)
    monkeypatch.setattr(network, "_fetch_bytes", fetch)
    with pytest.raises(network.BlueskyError):
        network.download_blob(url, 1000)
    resolve.assert_not_called()
    fetch.assert_not_called()
