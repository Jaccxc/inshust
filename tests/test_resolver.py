"""Behavior of the injectable MediaResolver (ShareRef -> MediaKey).

We assert through the public ``resolve`` method using a FakeClient (no network)
and a real temp SQLite store, never by inspecting internals. The resolver must:
use a ref's pk directly without any client call; resolve a url-only ref via the
client and cache it; reuse the cache on a repeat url (no 2nd client call); and
surface a resolution failure so the caller can skip gracefully.
"""

from inshust.client import FakeClient
from inshust.media_identity import ShareRef
from inshust.models import MEDIA_TYPE_CLIP, MEDIA_TYPE_XMA, MediaKey
from inshust.resolver import MediaResolver
from inshust.store import Store


def _store(tmp_path):
    return Store(str(tmp_path / "db.sqlite"))


def test_ref_with_pk_resolves_directly_without_client_call(tmp_path):
    client = FakeClient(url_to_pk={})
    resolver = MediaResolver(client, _store(tmp_path))
    ref = ShareRef(pk="100", shortcode="CzAbc", media_type=MEDIA_TYPE_CLIP)
    key = resolver.resolve(ref)
    assert key == MediaKey(pk="100", shortcode="CzAbc", media_type=MEDIA_TYPE_CLIP)
    assert client.resolve_calls == 0  # pk present: never touches the client


def test_url_only_ref_resolves_via_client(tmp_path):
    client = FakeClient(url_to_pk={"https://www.instagram.com/reel/CzAbc/": "100"})
    resolver = MediaResolver(client, _store(tmp_path))
    ref = ShareRef(
        pk=None,
        shortcode=None,
        media_type=MEDIA_TYPE_XMA,
        url="https://www.instagram.com/reel/CzAbc/",
    )
    key = resolver.resolve(ref)
    assert key is not None
    assert key.pk == "100"
    assert client.resolve_calls == 1


def test_repeat_url_uses_cache_no_second_client_call(tmp_path):
    client = FakeClient(url_to_pk={"u1": "100"})
    resolver = MediaResolver(client, _store(tmp_path))
    ref = ShareRef(pk=None, shortcode=None, media_type=MEDIA_TYPE_XMA, url="u1")
    first = resolver.resolve(ref)
    second = resolver.resolve(ref)
    assert first.pk == "100"
    assert second.pk == "100"
    assert client.resolve_calls == 1  # cached: only the first call hit the client


def test_cache_is_shared_across_resolver_instances_via_store(tmp_path):
    # A fresh resolver over the SAME store reuses the persisted cache.
    store = _store(tmp_path)
    client = FakeClient(url_to_pk={"u1": "100"})
    MediaResolver(client, store).resolve(
        ShareRef(pk=None, shortcode=None, media_type=MEDIA_TYPE_XMA, url="u1")
    )
    client2 = FakeClient(url_to_pk={"u1": "999-unused"})
    key = MediaResolver(client2, store).resolve(
        ShareRef(pk=None, shortcode=None, media_type=MEDIA_TYPE_XMA, url="u1")
    )
    assert key.pk == "100"  # from the persisted cache, not the new client map
    assert client2.resolve_calls == 0


def test_unresolvable_url_returns_none(tmp_path):
    client = FakeClient(url_to_pk={})  # cannot resolve anything
    resolver = MediaResolver(client, _store(tmp_path))
    ref = ShareRef(pk=None, shortcode=None, media_type=MEDIA_TYPE_XMA, url="unknown")
    assert resolver.resolve(ref) is None  # failure surfaced as None, no crash
