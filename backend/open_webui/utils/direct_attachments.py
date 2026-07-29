import copy
import html
import logging
from typing import Any

from open_webui.models.files import Files
from open_webui.utils.access_control.files import has_access_to_file

log = logging.getLogger(__name__)

DIRECT_ATTACHMENT_PURPOSE = 'chat_attachment'


def is_direct_attachment(item: object) -> bool:
    if not isinstance(item, dict) or item.get('purpose') != DIRECT_ATTACHMENT_PURPOSE:
        return False

    content_type = item.get('content_type') or ''
    return item.get('type') in {'file', 'text'} and not content_type.startswith('image/')


def _file_key(item: dict) -> str:
    return str(item.get('id') or item.get('itemId') or (item.get('name'), item.get('content')))


def _dedupe_files(files: list[dict]) -> list[dict]:
    deduped = {}
    for item in files:
        deduped[_file_key(item)] = item
    return list(deduped.values())


def collect_direct_attachment_contexts(messages: list[dict]) -> list[dict]:
    user_messages = [message for message in messages if message.get('role') == 'user']
    contexts = []

    for user_message_index, message in enumerate(user_messages):
        files = [copy.deepcopy(item) for item in message.get('files', []) if is_direct_attachment(item)]
        if files:
            contexts.append(
                {
                    'message_id': message.get('id'),
                    'user_message_index': user_message_index,
                    'user_message_count': len(user_messages),
                    'files': _dedupe_files(files),
                }
            )

    return contexts


def clear_direct_attachment_contexts(form_data: dict) -> dict:
    metadata = form_data.get('metadata')
    if isinstance(metadata, dict):
        metadata.pop('direct_attachment_contexts', None)

    for message in form_data.get('messages', []):
        message.pop('files', None)

    return form_data


def _format_attachment(name: str, content: str) -> str:
    safe_name = html.escape(name.replace('\r', ' ').replace('\n', ' '), quote=True)
    return f'<attached_file name="{safe_name}">\n{content}\n</attached_file>'


def _append_text_content(message: dict, text: str) -> None:
    content = message.get('content', '')
    if isinstance(content, list):
        for item in content:
            if item.get('type') == 'text':
                existing = item.get('text', '')
                item['text'] = f'{existing}\n\n{text}' if existing else text
                return
        content.insert(0, {'type': 'text', 'text': text})
    else:
        message['content'] = f'{content}\n\n{text}' if content else text


async def hydrate_direct_attachments(messages: list[dict], contexts: list[dict], user) -> list[dict]:
    user_messages = [message for message in messages if message.get('role') == 'user']
    file_cache: dict[str, Any] = {}

    for context in contexts or []:
        index = context.get('user_message_index')
        if not isinstance(index, int) or not 0 <= index < len(user_messages):
            continue

        attachments = []
        for item in _dedupe_files(context.get('files', [])):
            name = item.get('name') or 'attachment'
            content = item.get('content') if item.get('type') == 'text' else None

            file_id = item.get('id')
            if content is None and file_id:
                if file_id not in file_cache:
                    file_cache[file_id] = await Files.get_file_by_id(file_id)
                file = file_cache[file_id]
                if not file:
                    log.warning('Direct attachment file not found: %s', file_id)
                    continue

                can_read = (
                    file.user_id == user.id or user.role == 'admin' or await has_access_to_file(file_id, 'read', user)
                )
                if not can_read:
                    log.warning('Direct attachment access denied: %s', file_id)
                    continue

                file_data = file.data or {}
                if file_data.get('status') == 'failed':
                    log.warning('Direct attachment extraction failed: %s', file_id)
                    continue

                content = file_data.get('content')
                name = item.get('name') or file.filename

            if not isinstance(content, str) or not content:
                log.warning('Direct attachment has no extracted text: %s', file_id or name)
                continue

            attachments.append(_format_attachment(name, content))

        if attachments:
            _append_text_content(
                user_messages[index], '<attached_files>\n' + '\n'.join(attachments) + '\n</attached_files>'
            )

    return messages


async def hydrate_direct_attachments_for_compaction(
    messages: list[dict],
    user,
    *,
    file_handler_owns_attachments: bool,
) -> list[dict]:
    if file_handler_owns_attachments:
        return messages

    contexts = collect_direct_attachment_contexts(messages)
    if not contexts:
        return messages

    return await hydrate_direct_attachments(messages, contexts, user)
