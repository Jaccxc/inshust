"""Behavior of the in-memory FakeClient (the test double for the real adapter).

The real instagrapi adapter (cards #3/#4) implements the same ClientPort; here
we only assert the fake records what it was asked to send.
"""

from inshust.client import ClientPort, FakeClient


def test_fake_client_records_sent_replies():
    client = FakeClient()
    assert client.sent == []
    client.send_reply(
        thread_id="t-1",
        text="already sent by @alice on 2023-11-15 06:13",
        original_message_id="m1",
        original_client_context="ctx-1",
    )
    assert len(client.sent) == 1
    sent = client.sent[0]
    assert sent.thread_id == "t-1"
    assert sent.text.startswith("already sent by @alice")
    assert sent.original_message_id == "m1"
    assert sent.original_client_context == "ctx-1"


def test_fake_client_satisfies_the_port():
    assert isinstance(FakeClient(), ClientPort)
