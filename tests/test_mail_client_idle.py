import pytest
import threading

from astrbot_plugin_telegram_mail import mail_client
from astrbot_plugin_telegram_mail.mail_client import MailClient


class FakeSocket:
    def __init__(self):
        self.timeout = None

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value


class FakeImap:
    def __init__(self, lines):
        self.lines = list(lines)
        self.sent = []
        self.sock = FakeSocket()
        self.tagged_commands = {}
        self.tagnum = 0

    def _new_tag(self):
        tag = b"A" + str(self.tagnum).encode("ascii")
        self.tagnum += 1
        self.tagged_commands[tag] = None
        return tag

    def send(self, data):
        self.sent.append(data)

    def _get_line(self):
        return self.lines.pop(0)


def test_supports_idle_when_capability_contains_idle():
    class Client:
        def capability(self):
            return "OK", [b"IMAP4rev1 IDLE UIDPLUS"]

    assert MailClient._supports_idle(Client())


def test_does_not_support_idle_when_capability_omits_idle():
    class Client:
        def capability(self):
            return "OK", [b"IMAP4rev1 UIDPLUS"]

    assert not MailClient._supports_idle(Client())


def test_idle_wait_returns_true_on_exists(monkeypatch):
    client = FakeImap([b"+ idling", b"* 2 EXISTS", b"A0 OK IDLE terminated"])
    monkeypatch.setattr(mail_client.select, "select", lambda r, w, x, t: (r, [], []))

    assert MailClient._idle_wait(client, 10) is True
    assert client.sent == [b"A0 IDLE\r\n", b"DONE\r\n"]


def test_idle_wait_returns_false_on_timeout(monkeypatch):
    client = FakeImap([b"+ idling", b"A0 OK IDLE terminated"])
    monkeypatch.setattr(mail_client.select, "select", lambda r, w, x, t: ([], [], []))

    assert MailClient._idle_wait(client, 10) is False
    assert client.sent == [b"A0 IDLE\r\n", b"DONE\r\n"]


def test_idle_wait_continues_until_timeout(monkeypatch):
    client = FakeImap([b"+ idling", b"* 3 EXISTS", b"A0 OK IDLE terminated"])
    calls = []

    def fake_select(r, w, x, t):
        calls.append(t)
        if len(calls) == 1:
            return [], [], []
        return r, [], []

    monkeypatch.setattr(mail_client.select, "select", fake_select)

    assert MailClient._idle_wait(client, 10, check_interval=1) is True
    assert calls[0] == 1
    assert client.sent == [b"A0 IDLE\r\n", b"DONE\r\n"]


def test_idle_wait_stops_when_stop_event_is_set(monkeypatch):
    client = FakeImap([b"+ idling", b"A0 OK IDLE terminated"])
    stop_event = threading.Event()

    def fake_select(r, w, x, t):
        stop_event.set()
        return [], [], []

    monkeypatch.setattr(mail_client.select, "select", fake_select)

    assert MailClient._idle_wait(client, 10, 1, stop_event) is False
    assert client.sent == [b"A0 IDLE\r\n", b"DONE\r\n"]


def test_idle_wait_raises_when_server_rejects_idle():
    client = FakeImap([b"A0 BAD IDLE unsupported"])

    with pytest.raises(RuntimeError, match="Failed to enter IMAP IDLE"):
        MailClient._idle_wait(client, 10)
