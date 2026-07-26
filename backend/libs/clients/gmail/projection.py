# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Decode Gmail MIME payloads into compact body and attachment records."""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import formataddr, getaddresses, parseaddr
from html.parser import HTMLParser
from typing import Any, Literal, NotRequired, TypedDict, cast

from libs.clients.compact import (
    ATTACHMENT_LIMIT,
    BODY_CHAR_LIMIT,
    advisory,
    bound_items,
    truncate_text,
)
from libs.clients.gmail.errors import GmailAPIError

_BLOCK_TAGS = {
    'address',
    'article',
    'aside',
    'blockquote',
    'br',
    'div',
    'dl',
    'dt',
    'dd',
    'footer',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'header',
    'hr',
    'li',
    'main',
    'nav',
    'ol',
    'p',
    'pre',
    'section',
    'table',
    'tr',
    'ul',
}
_SKIPPED_TAGS = {'script', 'style'}
# Bound provider-controlled recursion well below Python's interpreter limit.
_MAX_MIME_DEPTH = 50
_GMAIL_AUTHSERV_ID = 'mx.google.com'
_PROJECTED_HEADERS = {
    'from': 'from',
    'to': 'to',
    'cc': 'cc',
    'bcc': 'bcc',
    'reply-to': 'reply_to',
    'return-path': 'return_path',
    'subject': 'subject',
    'date': 'date',
    'message-id': 'message_id',
    'in-reply-to': 'in_reply_to',
    'references': 'references',
}
_AUTH_RESULT_RE = re.compile(r'(?i)^\s*(spf|dkim|dmarc|arc)\s*=\s*([^\s(;]+)(?=$|[\s(])')


Verdict = Literal['pass', 'fail', 'softfail', 'neutral', 'temperror', 'permerror', 'unknown']
DMARCPolicy = Literal['none', 'quarantine', 'reject']
BodySource = Literal['plain', 'html_to_text']
_BodySection = tuple[str, BodySource]


class SPFProjection(TypedDict):
    """Describe the normalized SPF verdict and envelope domain."""

    verdict: Verdict
    domain: str | None


class DKIMProjection(TypedDict):
    """Describe one normalized DKIM verdict and signing domain."""

    verdict: Verdict
    domain: str | None


class DMARCProjection(TypedDict):
    """Describe the normalized DMARC verdict, policy, and evaluated domain."""

    verdict: Verdict
    policy: DMARCPolicy | None
    header_from: str | None


class ARCProjection(TypedDict):
    """Describe the normalized ARC chain verdict."""

    verdict: Verdict


class AlignmentProjection(TypedDict):
    """Describe exact-host alignment for message mailbox domains."""

    from_domain: str | None
    reply_to_domain: str | None
    return_path_domain: str | None
    from_matches_reply_to: bool | None
    from_matches_return_path: bool | None


class AuthenticationProjection(TypedDict):
    """Define the locked public Gmail authentication projection shape."""

    spf: SPFProjection
    dkim: list[DKIMProjection]
    dmarc: DMARCProjection
    arc: ARCProjection
    alignment: AlignmentProjection


class AttachmentProjection(TypedDict):
    """Define one fetchable attachment in the locked public projection."""

    attachment_id: str
    filename: str
    mime_type: str
    size: int | None


class AttachmentMetaProjection(TypedDict):
    """Define explicit attachment bounding counts."""

    truncated: bool
    included: int
    total: int
    omitted_count: int


class AdvisoryProjection(TypedDict):
    """Define one compact projection advisory."""

    code: str
    message: str


class MessageBodyProjection(TypedDict):
    """Define bounded decoded body text and its derivation."""

    text: str
    source: BodySource


class MessageRefProjection(TypedDict):
    """Define the stable fetch reference for omitted message content."""

    service: Literal['gmail']
    resource_type: Literal['message']
    resource_id: str


class BodyTruncationProjection(TypedDict):
    """Define omitted body suffix metadata."""

    truncated: Literal[True]
    omitted_chars: int
    ref: NotRequired[MessageRefProjection]


class LabelProjection(TypedDict):
    """Define one compact Gmail label identity record."""

    id: str
    name: str
    type: NotRequired[str]


class AttachmentContentProjection(TypedDict):
    """Define decoded attachment content in JSON-safe standard base64 form."""

    attachment_id: str
    size: int
    mime_type: str | None
    data_base64: str


class MutationAckProjection(TypedDict):
    """Define the compact acknowledgement returned by a Gmail mutation."""

    ok: Literal[True]
    message_id: str
    label_ids: NotRequired[list[str]]


MessageSummaryProjection = TypedDict(
    'MessageSummaryProjection',
    {
        'id': NotRequired[str],
        'thread_id': NotRequired[str],
        'label_ids': NotRequired[list[str]],
        'from': NotRequired[str],
        'to': NotRequired[list[str]],
        'cc': NotRequired[list[str]],
        'reply_to': NotRequired[str],
        'return_path': NotRequired[str],
        'subject': NotRequired[str],
        'message_id': NotRequired[str],
        'date': NotRequired[str],
        'received_at': NotRequired[str],
        'snippet': NotRequired[str],
        'has_attachments': bool,
        'attachments': list[AttachmentProjection],
        'attachments_meta': AttachmentMetaProjection,
        'authentication': AuthenticationProjection,
        'advisories': list[AdvisoryProjection],
    },
)


MessageFullProjection = TypedDict(
    'MessageFullProjection',
    {
        'id': NotRequired[str],
        'thread_id': NotRequired[str],
        'label_ids': NotRequired[list[str]],
        'from': NotRequired[str],
        'to': NotRequired[list[str]],
        'cc': NotRequired[list[str]],
        'reply_to': NotRequired[str],
        'return_path': NotRequired[str],
        'subject': NotRequired[str],
        'message_id': NotRequired[str],
        'date': NotRequired[str],
        'received_at': NotRequired[str],
        'snippet': NotRequired[str],
        'has_attachments': bool,
        'attachments': list[AttachmentProjection],
        'attachments_meta': AttachmentMetaProjection,
        'authentication': AuthenticationProjection,
        'advisories': list[AdvisoryProjection],
        'body': MessageBodyProjection,
        'body_truncation': NotRequired[BodyTruncationProjection],
    },
)


def project_labels(raw: object) -> list[LabelProjection]:
    """Project a provider label list to validated identity-only records."""
    if not isinstance(raw, list):
        return []
    projected: list[LabelProjection] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        label_id = item.get('id')
        name = item.get('name')
        if not isinstance(label_id, str) or not label_id or not isinstance(name, str) or not name:
            continue
        label: LabelProjection = {'id': label_id, 'name': name}
        label_type = item.get('type')
        if isinstance(label_type, str) and label_type:
            label['type'] = label_type
        projected.append(label)
    return projected


def project_attachment(raw: Mapping[str, Any]) -> AttachmentContentProjection:
    """Encode validated GmailClient attachment bytes without provider wire data."""
    attachment_id = raw.get('attachment_id')
    mime_type = raw.get('mime_type')
    data = raw.get('data')
    if (
        not isinstance(attachment_id, str)
        or not attachment_id
        or (mime_type is not None and not isinstance(mime_type, str))
        or not isinstance(data, bytes)
    ):
        raise GmailAPIError('Invalid Gmail attachment response')
    return {
        'attachment_id': attachment_id,
        'size': len(data),
        'mime_type': mime_type,
        'data_base64': base64.b64encode(data).decode('ascii'),
    }


def project_mutation_ack(raw: Mapping[str, Any], *, message_id: str) -> MutationAckProjection:
    """Project a mutation response, falling back to the caller's message identity."""
    provider_message_id = raw.get('id')
    projected_message_id = provider_message_id.strip() if isinstance(provider_message_id, str) else ''
    if not projected_message_id:
        projected_message_id = message_id.strip() if isinstance(message_id, str) else ''
    if not projected_message_id:
        raise GmailAPIError('Invalid Gmail mutation response')
    projected: MutationAckProjection = {
        'ok': True,
        'message_id': projected_message_id,
    }
    label_ids = raw.get('labelIds')
    if isinstance(label_ids, list) and all(isinstance(item, str) and item.strip() for item in label_ids):
        projected['label_ids'] = [item.strip() for item in label_ids]
    return projected


class _ReadableHTMLParser(HTMLParser):
    """Collect visible HTML text while retaining basic block separation."""

    def __init__(self) -> None:
        """Initialize a parser that converts character references automatically."""
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skipped_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start visible blocks and suppress script or style subtrees."""
        del attrs
        normalized = tag.lower()
        if normalized in _SKIPPED_TAGS:
            self._skipped_depth += 1
        elif self._skipped_depth == 0 and normalized in _BLOCK_TAGS:
            self.chunks.append('\n')

    def handle_endtag(self, tag: str) -> None:
        """End suppressed subtrees or append separation after visible blocks."""
        normalized = tag.lower()
        if normalized in _SKIPPED_TAGS and self._skipped_depth:
            self._skipped_depth -= 1
        elif self._skipped_depth == 0 and normalized in _BLOCK_TAGS:
            self.chunks.append('\n')

    def handle_data(self, data: str) -> None:
        """Collect only text outside script and style elements."""
        if self._skipped_depth == 0:
            self.chunks.append(data)


def _decode_header_value(value: str) -> str:
    """Decode RFC 2047 words, replacing undecodable bytes without rejecting a header."""
    try:
        chunks = decode_header(value)
    except (LookupError, TypeError, UnicodeError, ValueError):
        return value

    decoded: list[str] = []
    for chunk, charset in chunks:
        if isinstance(chunk, str):
            decoded.append(chunk)
            continue
        try:
            decoded.append(chunk.decode(charset or 'ascii', errors='replace'))
        except LookupError:
            decoded.append(chunk.decode('utf-8', errors='replace'))
    return ''.join(decoded)


def _header_values(headers: object, name: str) -> list[str]:
    """Return ordered string values for one case-insensitive Gmail header name."""
    if not isinstance(headers, list):
        return []
    values: list[str] = []
    for item in headers:
        if not isinstance(item, Mapping):
            continue
        item_name = item.get('name')
        value = item.get('value')
        if isinstance(item_name, str) and item_name.lower() == name.lower() and isinstance(value, str):
            values.append(value)
    return values


def project_headers(headers: object) -> dict[str, str]:
    """Project safe common Gmail headers, assuming provider records use name/value pairs."""
    projected: dict[str, str] = {}
    if not isinstance(headers, list):
        return projected
    for item in headers:
        if not isinstance(item, Mapping):
            continue
        name = item.get('name')
        value = item.get('value')
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        key = _PROJECTED_HEADERS.get(name.lower())
        if key is not None and key not in projected:
            projected[key] = _decode_header_value(value)
    return projected


def _mailbox_domain(value: str | None) -> str | None:
    """Extract one lowercase mailbox domain; malformed or domainless addresses yield null."""
    if not value:
        return None
    _display_name, address = parseaddr(_decode_header_value(value))
    if '@' not in address:
        return None
    local_part, domain = address.rsplit('@', 1)
    domain = domain.strip().lower().rstrip('.')
    if not local_part or not _valid_domain(domain):
        return None
    return domain


def _valid_domain(domain: str) -> bool:
    """Accept a bounded DNS host whose labels are non-empty and syntactically valid."""
    if not domain or len(domain) > 253:
        return False
    labels = domain.split('.')
    return all(
        len(label) <= 63 and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) is not None for label in labels
    )


def _parameter(segment: str, name: str) -> str | None:
    """Read one token parameter from an authentication-result mechanism segment."""
    match = re.search(
        rf'(?i)(?:^|[\s(;]){re.escape(name)}\s*=\s*' rf'("[^"]*"|<[^>]*>|[^\s;()"<>]+)(?=$|[\s;()])',
        segment,
    )
    if match is None:
        return None
    return match.group(1).strip().strip('"<>').rstrip('.,').strip() or None


def _without_comments(segment: str) -> str:
    """Remove balanced authentication comments so they cannot supply identity evidence."""
    visible: list[str] = []
    depth = 0
    quoted = False
    escaped = False
    for character in segment:
        if escaped:
            if depth == 0:
                visible.append(character)
            escaped = False
            continue
        if character == '\\' and (quoted or depth):
            if depth == 0:
                visible.append(character)
            escaped = True
        elif character == '"' and depth == 0:
            quoted = not quoted
            visible.append(character)
        elif not quoted and character == '(':
            depth += 1
            if depth == 1:
                visible.append(' ')
        elif not quoted and character == ')' and depth:
            depth -= 1
        elif depth == 0:
            visible.append(character)
    return ''.join(visible)


def _domain_parameter(segment: str, *names: str) -> str | None:
    """Normalize the first non-comment mailbox-or-domain identity parameter."""
    visible_segment = _without_comments(segment)
    for name in names:
        value = _parameter(visible_segment, name)
        if value is None:
            continue
        mailbox_domain = _mailbox_domain(value)
        if mailbox_domain is not None:
            return mailbox_domain
        domain = value.lower().rstrip('.')
        if _valid_domain(domain):
            return domain
    return None


def _result_fields(value: str) -> list[str] | None:
    """Split balanced authentication syntax on top-level semicolons, rejecting malformed input."""
    fields: list[str] = []
    start = 0
    comment_depth = 0
    angle_depth = 0
    quoted = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == '\\' and (quoted or comment_depth):
            escaped = True
        elif character == '"' and comment_depth == 0:
            quoted = not quoted
        elif not quoted and character == '(':
            comment_depth += 1
        elif not quoted and character == ')':
            if comment_depth == 0:
                return None
            comment_depth -= 1
        elif not quoted and comment_depth == 0 and character == '<':
            if angle_depth:
                return None
            angle_depth = 1
        elif not quoted and comment_depth == 0 and character == '>':
            if angle_depth == 0:
                return None
            angle_depth = 0
        elif character == ';' and not quoted and comment_depth == 0 and angle_depth == 0:
            fields.append(value[start:index])
            start = index + 1
    if escaped or quoted or comment_depth or angle_depth:
        return None
    fields.append(value[start:])
    return fields


def _mechanisms(value: str) -> list[tuple[str, str, str]]:
    """Parse only semicolon-delimited fields that begin with an authentication mechanism."""
    mechanisms: list[tuple[str, str, str]] = []
    fields = _result_fields(value)
    if fields is None:
        return mechanisms
    for field in fields:
        match = _AUTH_RESULT_RE.match(field)
        if match is not None:
            mechanisms.append((match.group(1).lower(), match.group(2).lower(), field[match.end() :]))
    return mechanisms


def _verdict(value: str) -> Verdict:
    """Preserve standardized authentication verdicts and collapse all others to unknown."""
    if value == 'pass':
        return 'pass'
    if value == 'fail':
        return 'fail'
    if value == 'softfail':
        return 'softfail'
    if value == 'neutral':
        return 'neutral'
    if value == 'temperror':
        return 'temperror'
    if value == 'permerror':
        return 'permerror'
    return 'unknown'


def _dmarc_policy(value: str | None) -> DMARCPolicy | None:
    """Allow only standardized DMARC policy and disposition values."""
    normalized = value.lower() if value is not None else None
    if normalized == 'none':
        return 'none'
    if normalized == 'quarantine':
        return 'quarantine'
    if normalized == 'reject':
        return 'reject'
    return None


def _trusted_authentication_results(values: list[str]) -> list[tuple[str, str, str]]:
    """Parse only Gmail receiver results with the exact trusted authserv identifier."""
    trusted: list[tuple[str, str, str]] = []
    for value in values:
        fields = _result_fields(value)
        if fields is None or not fields or fields[0].strip().lower() != _GMAIL_AUTHSERV_ID:
            continue
        trusted.extend(_mechanisms(';'.join(fields[1:])))
    return trusted


def _spf_result(results: list[tuple[str, str, str]]) -> SPFProjection | None:
    """Return the first complete SPF result from parsed mechanism evidence."""
    for mechanism, raw_verdict, segment in results:
        if mechanism != 'spf':
            continue
        domain = _domain_parameter(segment, 'smtp.mailfrom', 'envelope-from')
        verdict = _verdict(raw_verdict)
        if verdict != 'unknown' and domain is not None:
            return {'verdict': verdict, 'domain': domain}
    return None


def _dkim_results(results: list[tuple[str, str, str]]) -> list[DKIMProjection]:
    """Retain every DKIM result in source order, requiring a signing domain for trust."""
    projected: list[DKIMProjection] = []
    for mechanism, raw_verdict, segment in results:
        if mechanism != 'dkim':
            continue
        domain = _domain_parameter(segment, 'header.d')
        verdict = _verdict(raw_verdict)
        projected.append(
            {
                'verdict': verdict if domain is not None else 'unknown',
                'domain': domain,
            }
        )
    return projected


def _dmarc_result(results: list[tuple[str, str, str]]) -> DMARCProjection | None:
    """Return the first DMARC result, requiring its evaluated header-from domain."""
    for mechanism, raw_verdict, segment in results:
        if mechanism != 'dmarc':
            continue
        header_from = _domain_parameter(segment, 'header.from')
        verdict = _verdict(raw_verdict)
        policy = _parameter(segment, 'p') or _parameter(segment, 'policy')
        if policy is None:
            policy = _parameter(segment, 'dis') or _parameter(segment, 'action')
        return {
            'verdict': verdict if header_from is not None else 'unknown',
            'policy': _dmarc_policy(policy),
            'header_from': header_from,
        }
    return None


def _arc_result(results: list[tuple[str, str, str]]) -> ARCProjection | None:
    """Return the first standardized ARC chain verdict."""
    for mechanism, raw_verdict, _segment in results:
        if mechanism == 'arc':
            return {'verdict': _verdict(raw_verdict)}
    return None


def _domains_match(left: str | None, right: str | None) -> bool | None:
    """Compare exact lowercase hosts, returning null when either mailbox is absent."""
    if left is None or right is None:
        return None
    return left == right


def project_authentication(headers: object) -> AuthenticationProjection:
    """Project deterministic delivery-auth signals without returning raw provider evidence."""
    authentication_results = _trusted_authentication_results(_header_values(headers, 'Authentication-Results'))

    spf: SPFProjection = _spf_result(authentication_results) or {'verdict': 'unknown', 'domain': None}
    dmarc: DMARCProjection = _dmarc_result(authentication_results) or {
        'verdict': 'unknown',
        'policy': None,
        'header_from': None,
    }
    arc = _arc_result(authentication_results) or {'verdict': 'unknown'}

    from_domain = _mailbox_domain(next(iter(_header_values(headers, 'From')), None))
    reply_to_domain = _mailbox_domain(next(iter(_header_values(headers, 'Reply-To')), None))
    return_path_domain = _mailbox_domain(next(iter(_header_values(headers, 'Return-Path')), None))
    return {
        'spf': spf,
        'dkim': _dkim_results(authentication_results),
        'dmarc': dmarc,
        'arc': arc,
        'alignment': {
            'from_domain': from_domain,
            'reply_to_domain': reply_to_domain,
            'return_path_domain': return_path_domain,
            'from_matches_reply_to': _domains_match(from_domain, reply_to_domain),
            'from_matches_return_path': _domains_match(from_domain, return_path_domain),
        },
    }


def html_to_text(value: str) -> str:
    """Convert HTML to readable visible text without exposing markup attributes."""
    parser = _ReadableHTMLParser()
    parser.feed(value)
    parser.close()
    text = ''.join(parser.chunks).replace('\r', '\n')
    text = re.sub(r'[^\S\n]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{2,}', '\n', text).strip()


def _header(part: Mapping[str, Any], name: str) -> str | None:
    """Return one case-insensitive string header from a Gmail MIME part."""
    headers = part.get('headers')
    if not isinstance(headers, list):
        return None
    for item in headers:
        if not isinstance(item, Mapping):
            continue
        header_name = item.get('name')
        value = item.get('value')
        if isinstance(header_name, str) and header_name.lower() == name.lower() and isinstance(value, str):
            return value
    return None


def _mime_type(part: Mapping[str, Any]) -> str:
    """Resolve a normalized MIME type from Gmail metadata or Content-Type."""
    mime_type = part.get('mimeType')
    if isinstance(mime_type, str) and mime_type.strip():
        return mime_type.split(';', 1)[0].strip().lower()
    content_type = _header(part, 'Content-Type')
    if content_type:
        return content_type.split(';', 1)[0].strip().lower()
    return ''


def _charset(part: Mapping[str, Any]) -> str:
    """Resolve the declared charset, defaulting MIME text to UTF-8."""
    content_type = _header(part, 'Content-Type')
    if not content_type:
        mime_type = part.get('mimeType')
        content_type = mime_type if isinstance(mime_type, str) else None
    if not content_type:
        return 'utf-8'
    message = Message()
    message['Content-Type'] = content_type
    return message.get_content_charset() or 'utf-8'


def _decode_gmail_data(value: Any) -> bytes:
    """Strictly decode Gmail's unpadded base64url body representation."""
    if not isinstance(value, str):
        raise ValueError('Gmail body data must be text')
    encoded = value.encode('ascii')
    encoded += b'=' * (-len(encoded) % 4)
    return base64.b64decode(encoded, altchars=b'-_', validate=True)


def _decode_text_part(part: Mapping[str, Any]) -> str:
    """Decode final Gmail part bytes, replacing bytes invalid for their charset."""
    body = part.get('body')
    if not isinstance(body, Mapping) or 'data' not in body:
        raise ValueError('text part has no inline data')
    value = _decode_gmail_data(body.get('data'))
    charset = _charset(part)
    try:
        return value.decode(charset, errors='replace')
    except LookupError:
        return value.decode('utf-8', errors='replace')


def _is_attachment(part: Mapping[str, Any]) -> bool:
    """Treat a non-empty filename or attachment disposition as authoritative."""
    filename = part.get('filename')
    if isinstance(filename, str) and filename.strip():
        return True
    disposition = _header(part, 'Content-Disposition')
    return bool(disposition and disposition.split(';', 1)[0].strip().lower() == 'attachment')


def _attachment_filename(part: Mapping[str, Any]) -> str:
    """Resolve a decoded attachment filename, allowing unnamed disposition attachments."""
    filename = part.get('filename')
    if isinstance(filename, str) and filename.strip():
        return filename
    disposition = _header(part, 'Content-Disposition')
    if not disposition:
        return ''
    message = Message()
    message['Content-Disposition'] = disposition
    header_filename = message.get_filename()
    return _decode_header_value(header_filename) if isinstance(header_filename, str) else ''


def _attachment(part: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project metadata only for a marked attachment with a stable Gmail id."""
    if not _is_attachment(part):
        return None
    body = part.get('body')
    if not isinstance(body, Mapping):
        return None
    attachment_id = body.get('attachmentId')
    if not isinstance(attachment_id, str) or not attachment_id:
        return None
    return {
        'attachment_id': attachment_id,
        'filename': _attachment_filename(part),
        'mime_type': _mime_type(part),
        'size': body.get('size'),
    }


def _handle_attachment(
    part: Mapping[str, Any],
    *,
    attachments: list[dict[str, Any]],
    advisories: list[dict[str, str]],
) -> bool:
    """Consume every marked attachment, advising when it cannot be represented."""
    if not _is_attachment(part):
        return False
    attachment = _attachment(part)
    if attachment is not None:
        attachments.append(attachment)
    else:
        advisories.append(
            advisory(
                code='mime_part',
                message='Attachment without a stable attachment id was omitted.',
            )
        )
    return True


def _walk_part(
    part: Mapping[str, Any],
    *,
    attachments: list[dict[str, Any]],
    advisories: list[dict[str, str]],
    depth: int,
) -> list[_BodySection]:
    """Project one bounded MIME subtree into ordered independent body sections."""
    if depth > _MAX_MIME_DEPTH:
        advisories.append(
            advisory(
                code='mime_part',
                message='MIME nesting limit reached; deeper parts were omitted.',
            )
        )
        return []

    if _handle_attachment(part, attachments=attachments, advisories=advisories):
        return []

    children = part.get('parts')
    if isinstance(children, list):
        child_sections: list[list[_BodySection]] = []
        for child in children:
            if isinstance(child, Mapping):
                child_sections.append(
                    _walk_part(
                        child,
                        attachments=attachments,
                        advisories=advisories,
                        depth=depth + 1,
                    )
                )
            else:
                advisories.append(advisory(code='mime_part', message='Malformed MIME part was omitted.'))
        if _mime_type(part) == 'multipart/alternative':
            representations = [
                combined for sections in child_sections if (combined := _combine_sections(sections)) is not None
            ]
            selected = next((item for item in representations if item[1] == 'plain'), None)
            if selected is None:
                selected = next((item for item in representations if item[1] == 'html_to_text'), None)
            return [selected] if selected is not None else []
        return [section for sections in child_sections for section in sections]

    mime_type = _mime_type(part)
    if mime_type not in {'text/plain', 'text/html'}:
        advisories.append(advisory(code='mime_part', message='Unsupported MIME part was omitted.'))
        return []

    try:
        text = _decode_text_part(part)
    except (ArithmeticError, LookupError, TypeError, UnicodeError, ValueError):
        advisories.append(advisory(code='mime_part', message='Malformed MIME part was omitted.'))
        return []
    if mime_type == 'text/plain':
        return [(text, 'plain')]
    return [(html_to_text(text), 'html_to_text')]


def _combine_sections(sections: list[_BodySection]) -> _BodySection | None:
    """Combine non-empty ordered sections and report whether HTML conversion contributed."""
    non_empty = [section for section in sections if section[0].strip()]
    if not non_empty:
        return None
    source: BodySource = 'html_to_text' if any(section[1] == 'html_to_text' for section in non_empty) else 'plain'
    return '\n'.join(section[0] for section in non_empty), source


def decode_message_content(
    raw: Mapping[str, Any],
) -> tuple[dict[str, str] | None, list[dict[str, Any]], list[dict[str, str]]]:
    """Decode a bounded Gmail ``full`` MIME tree whose body data contains final base64url bytes."""
    attachments: list[dict[str, Any]] = []
    advisories: list[dict[str, str]] = []
    payload = raw.get('payload')
    if not isinstance(payload, Mapping):
        advisories.append(advisory(code='mime_part', message='Malformed MIME payload was omitted.'))
        return None, attachments, advisories

    sections = _walk_part(
        payload,
        attachments=attachments,
        advisories=advisories,
        depth=0,
    )
    body = _combine_sections(sections)
    if body is None:
        return None, attachments, advisories
    text, source = body
    return {'text': text, 'source': source}, attachments, advisories


def _walk_message_metadata(
    part: Mapping[str, Any],
    *,
    attachments: list[dict[str, Any]],
    advisories: list[dict[str, str]],
    depth: int,
) -> None:
    """Collect attachment metadata without interpreting unavailable message body bytes."""
    if depth > _MAX_MIME_DEPTH:
        advisories.append(
            advisory(
                code='mime_part',
                message='MIME nesting limit reached; deeper parts were omitted.',
            )
        )
        return

    if _handle_attachment(part, attachments=attachments, advisories=advisories):
        return

    children = part.get('parts')
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, Mapping):
            _walk_message_metadata(
                child,
                attachments=attachments,
                advisories=advisories,
                depth=depth + 1,
            )
        else:
            advisories.append(advisory(code='mime_part', message='Malformed MIME part was omitted.'))


def _decode_message_metadata(
    raw: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read summary-safe MIME metadata without requiring Gmail full-format body data."""
    attachments: list[dict[str, Any]] = []
    advisories: list[dict[str, str]] = []
    payload = raw.get('payload')
    if not isinstance(payload, Mapping):
        advisories.append(advisory(code='mime_part', message='Malformed MIME payload was omitted.'))
        return attachments, advisories
    _walk_message_metadata(payload, attachments=attachments, advisories=advisories, depth=0)
    return attachments, advisories


def _message_headers(raw: Mapping[str, Any]) -> object:
    """Return provider header records only when the Gmail payload is mapping-shaped."""
    payload = raw.get('payload')
    if not isinstance(payload, Mapping):
        return None
    return payload.get('headers')


def _address_list(value: str | None) -> list[str]:
    """Split one decoded mailbox header into stable decoded display-address strings."""
    if value is None:
        return []
    projected: list[str] = []
    for display_name, address in getaddresses([value]):
        if address:
            projected.append(_decode_header_value(formataddr((display_name, address))))
        elif display_name:
            projected.append(display_name)
    return projected


def _received_at(raw: Mapping[str, Any], date: str | None) -> str | None:
    """Prefer valid Gmail epoch milliseconds and otherwise mirror the decoded Date header."""
    internal_date = raw.get('internalDate')
    if isinstance(internal_date, str) and internal_date.isascii() and internal_date.isdecimal():
        try:
            seconds, milliseconds = divmod(int(internal_date), 1000)
            received_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                seconds=seconds,
                milliseconds=milliseconds,
            )
            return received_at.isoformat()
        except (OverflowError, ValueError):
            pass
    return date


def _project_attachment(value: Mapping[str, Any]) -> AttachmentProjection:
    """Copy only locked attachment metadata and normalize an invalid size to unknown."""
    size = value.get('size')
    return {
        'attachment_id': value['attachment_id'],
        'filename': value['filename'],
        'mime_type': value['mime_type'],
        'size': size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
    }


def _project_message_common(
    raw: Mapping[str, Any],
    *,
    decode_body: bool,
) -> tuple[MessageSummaryProjection, MessageBodyProjection | None]:
    """Build the shared bounded, allowlisted Gmail message projection."""
    header_records = _message_headers(raw)
    headers = project_headers(header_records)
    if decode_body:
        body, decoded_attachments, advisories = decode_message_content(raw)
    else:
        body = None
        decoded_attachments, advisories = _decode_message_metadata(raw)
    attachments = [_project_attachment(item) for item in decoded_attachments]
    bounded_attachments, attachments_meta = bound_items(attachments, limit=ATTACHMENT_LIMIT)

    projected: dict[str, Any] = {}
    provider_strings = {
        'id': 'id',
        'threadId': 'thread_id',
        'snippet': 'snippet',
    }
    for provider_key, projected_key in provider_strings.items():
        value = raw.get(provider_key)
        if isinstance(value, str):
            projected[projected_key] = value

    label_ids = raw.get('labelIds')
    if isinstance(label_ids, list):
        projected['label_ids'] = [item for item in label_ids if isinstance(item, str)]

    for key in ('from', 'reply_to', 'return_path', 'subject', 'message_id', 'date'):
        value = headers.get(key)
        if value is not None:
            projected[key] = value
    for key in ('to', 'cc'):
        addresses = _address_list(headers.get(key))
        if addresses:
            projected[key] = addresses

    received_at = _received_at(raw, headers.get('date'))
    if received_at is not None:
        projected['received_at'] = received_at

    projected.update(
        {
            'has_attachments': bool(decoded_attachments),
            'attachments': bounded_attachments,
            'attachments_meta': attachments_meta,
            'authentication': project_authentication(header_records),
            'advisories': advisories,
        }
    )
    return cast(MessageSummaryProjection, projected), cast(MessageBodyProjection | None, body)


def project_message_summary(raw: Mapping[str, Any]) -> MessageSummaryProjection:
    """Project a compact Gmail message summary with metadata but no body."""
    projected, _body = _project_message_common(raw, decode_body=False)
    return projected


def _empty_body_source(part: Mapping[str, Any], *, depth: int) -> BodySource | None:
    """Infer the decoder's source semantics from a bounded MIME metadata tree."""
    if depth > _MAX_MIME_DEPTH or _is_attachment(part):
        return None
    mime_type = _mime_type(part)
    if mime_type == 'text/plain':
        return 'plain'
    if mime_type == 'text/html':
        return 'html_to_text'

    children = part.get('parts')
    if not isinstance(children, list):
        return None
    sources = [
        source
        for child in children
        if isinstance(child, Mapping)
        if (source := _empty_body_source(child, depth=depth + 1)) is not None
    ]
    if mime_type == 'multipart/alternative':
        return 'plain' if 'plain' in sources else next(iter(sources), None)
    return 'html_to_text' if 'html_to_text' in sources else next(iter(sources), None)


def _empty_body(raw: Mapping[str, Any]) -> MessageBodyProjection:
    """Represent unavailable content using MIME semantics without provider fallback data."""
    payload = raw.get('payload')
    source = _empty_body_source(payload, depth=0) if isinstance(payload, Mapping) else None
    return {'text': '', 'source': source or 'plain'}


def project_message_full(raw: Mapping[str, Any]) -> MessageFullProjection:
    """Project a compact Gmail message with bounded decoded body content."""
    summary, body = _project_message_common(raw, decode_body=True)
    projected = cast(MessageFullProjection, summary)
    if body is None:
        body = _empty_body(raw)

    message_id = projected.get('id')
    ref = (
        {
            'service': 'gmail',
            'resource_type': 'message',
            'resource_id': message_id,
        }
        if isinstance(message_id, str)
        else None
    )
    text, truncation = truncate_text(body['text'], limit=BODY_CHAR_LIMIT, ref=ref)
    projected['body'] = {'text': text, 'source': body['source']}
    if truncation is not None:
        projected['body_truncation'] = cast(BodyTruncationProjection, truncation)
    return projected
