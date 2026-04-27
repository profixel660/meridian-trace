"""Email ingestion for .eml (stdlib email.parser) and .msg (extract_msg).

Both formats normalise to the same structured-text rendering:

    Subject: ...
    From: ...
    To: ...
    Sent: ...

    <body>

One chunk per message. .eml threads that contain multiple top-level message/rfc822
parts are split into one chunk per nested message; otherwise it's a single chunk.

Attachments are intentionally not extracted in v1. Treating attachments as their
own ingestible deliverables would require a follow-up pass that re-enters
ingest_file with the unpacked bytes, which is out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message as _EmailMessage
from email.parser import BytesParser
from email.policy import default as _default_policy
from pathlib import Path
from typing import Any

import extract_msg

from meridian.ingest._types import _Chunk, _Extracted


@dataclass
class _Message:
    subject: str
    sender: str
    to: str
    sent_at: str
    body: str


def _render(message: _Message) -> str:
    return (
        f"Subject: {message.subject}\n"
        f"From: {message.sender}\n"
        f"To: {message.to}\n"
        f"Sent: {message.sent_at}\n\n"
        f"{message.body}"
    )


def _eml_body(msg: _EmailMessage) -> str:
    """Prefer text/plain; fall back to text/html stripped of tags as a last resort."""
    if msg.is_multipart():
        # Walk parts; prefer the first text/plain we encounter.
        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.is_multipart():
                continue
            if ctype == "text/plain":
                try:
                    plain_parts.append(part.get_content())
                except (LookupError, KeyError):
                    payload = part.get_payload(decode=True) or b""
                    plain_parts.append(payload.decode(errors="replace"))
            elif ctype == "text/html":
                try:
                    html_parts.append(part.get_content())
                except (LookupError, KeyError):
                    payload = part.get_payload(decode=True) or b""
                    html_parts.append(payload.decode(errors="replace"))
        if plain_parts:
            return "\n\n".join(plain_parts)
        if html_parts:
            return "\n\n".join(html_parts)
        return ""
    # Single-part message.
    try:
        content = msg.get_content()
    except (LookupError, KeyError, AttributeError):
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            content = payload.decode(errors="replace")
        else:
            content = str(payload or "")
    return content if isinstance(content, str) else str(content)


def _eml_to_messages(parsed: _EmailMessage) -> list[_Message]:
    """Return one _Message per top-level message; nested message/rfc822 parts split."""
    nested: list[_EmailMessage] = []
    if parsed.is_multipart():
        for part in parsed.iter_parts():
            if part.get_content_type() == "message/rfc822":
                # Nested rfc822 parts wrap the inner message in a single payload.
                payload = part.get_payload()
                if isinstance(payload, list) and payload:
                    inner = payload[0]
                    if isinstance(inner, _EmailMessage):
                        nested.append(inner)
                elif isinstance(payload, _EmailMessage):
                    nested.append(payload)

    messages_source: list[_EmailMessage] = nested if nested else [parsed]
    out: list[_Message] = []
    for m in messages_source:
        out.append(
            _Message(
                subject=str(m.get("Subject", "") or ""),
                sender=str(m.get("From", "") or ""),
                to=str(m.get("To", "") or ""),
                sent_at=str(m.get("Date", "") or ""),
                body=_eml_body(m),
            )
        )
    return out


def _msg_to_message(msg: extract_msg.Message) -> _Message:
    sent = msg.date
    sent_str = sent.isoformat() if hasattr(sent, "isoformat") else (str(sent) if sent else "")
    return _Message(
        subject=str(msg.subject or ""),
        sender=str(msg.sender or ""),
        to=str(msg.to or ""),
        sent_at=sent_str,
        body=str(msg.body or ""),
    )


def extract(path: Path) -> _Extracted:
    """Extract a single email (or thread) into the standard _Extracted shape."""
    suffix = path.suffix.lower()
    messages: list[_Message] = []

    if suffix == ".eml":
        with path.open("rb") as fh:
            parsed = BytesParser(policy=_default_policy).parse(fh)
        # Some malformed eml files come back as a Message rather than EmailMessage;
        # fall back to message_from_bytes if needed (kept for safety).
        if not hasattr(parsed, "iter_parts"):
            with path.open("rb") as fh:
                parsed = message_from_bytes(fh.read(), policy=_default_policy)
        messages = _eml_to_messages(parsed)
    elif suffix == ".msg":
        with extract_msg.Message(str(path)) as m:
            messages = [_msg_to_message(m)]
    else:
        raise ValueError(f"email.extract: unsupported suffix {suffix!r} (expected .eml or .msg)")

    chunks: list[_Chunk] = []
    text_parts: list[str] = []
    for index, message in enumerate(messages):
        rendered = _render(message)
        text_parts.append(rendered)
        chunks.append(
            _Chunk(
                chunk_kind="email_message",
                locator={
                    "message_index": index,
                    "subject": message.subject,
                    "from": message.sender,
                    "sent_at": message.sent_at,
                },
                text=rendered,
            )
        )

    primary_subject = messages[0].subject if messages else ""
    metadata: dict[str, Any] = {
        "message_count": len(messages),
        "subject": primary_subject,
        "extraction_method": "eml_parsed",
    }

    return _Extracted(
        extraction_method="eml_parsed",
        text="\n\n---\n\n".join(text_parts),
        chunks=chunks,
        metadata=metadata,
    )


__all__ = ["extract"]
