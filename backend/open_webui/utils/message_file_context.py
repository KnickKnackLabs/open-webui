import copy
import json
from typing import Any


DOCUMENT_CONTEXT_FILE_TYPES = frozenset({'doc', 'text', 'note', 'chat', 'folder', 'collection', 'file', 'url'})


def is_document_context_file_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False

    item_type = item.get('type', 'file')
    content_type = item.get('content_type') or ''
    if item_type == 'image' or content_type.startswith('image/'):
        return False

    return (
        item_type in DOCUMENT_CONTEXT_FILE_TYPES
        or bool(item.get('docs'))
        or bool(item.get('collection_name'))
        or bool(item.get('collection_names'))
    )


def get_file_context_item_key(item: object) -> str:
    if not isinstance(item, dict):
        return repr(item)

    item_type = item.get('type', 'file')
    for key in ('id', 'collection_name', 'url', 'name'):
        value = item.get(key)
        if value is not None:
            return f'{item_type}:{key}:{value}'

    if item.get('collection_names') is not None:
        collection_names = json.dumps(item.get('collection_names'), sort_keys=True, default=str)
        return f'{item_type}:collection_names:{collection_names}'

    return json.dumps(item, sort_keys=True, default=str)


def dedupe_file_items(items: list[dict]) -> list[dict]:
    deduped = {}
    for item in items or []:
        deduped[json.dumps(item, sort_keys=True, default=str)] = item
    return list(deduped.values())


def get_user_messages(messages: list[dict]) -> list[dict]:
    return [message for message in messages if message.get('role') == 'user']


def clear_file_context_payload(form_data: dict) -> dict:
    metadata = form_data.get('metadata')
    if isinstance(metadata, dict):
        metadata.pop('files', None)
        metadata.pop('message_file_contexts', None)
    form_data.pop('files', None)
    return form_data


def collect_message_file_contexts(messages: list[dict]) -> list[dict]:
    contexts = []
    user_messages = get_user_messages(messages)

    for user_message_index, message in enumerate(user_messages):
        files = [copy.deepcopy(item) for item in message.get('files', []) if is_document_context_file_item(item)]
        if files:
            contexts.append(
                {
                    'message_id': message.get('id'),
                    'user_message_index': user_message_index,
                    'user_message_count': len(user_messages),
                    'files': dedupe_file_items(files),
                }
            )

    return contexts


def merge_message_file_contexts(*context_groups: list[dict]) -> list[dict]:
    merged = {}

    for contexts in context_groups:
        for context in contexts or []:
            message_id = context.get('message_id')
            key = (
                ('id', message_id)
                if message_id is not None
                else (
                    'position',
                    context.get('user_message_count'),
                    context.get('user_message_index'),
                )
            )
            if key in merged:
                previous = merged[key]
                merged[key] = {
                    **previous,
                    **copy.deepcopy(context),
                    'files': dedupe_file_items([*previous.get('files', []), *context.get('files', [])]),
                }
            else:
                merged[key] = copy.deepcopy(context)

    return list(merged.values())


def bind_message_file_contexts(contexts: list[dict], messages: list[dict]) -> list[dict]:
    user_messages = get_user_messages(messages)
    message_indices = {
        message.get('id'): index for index, message in enumerate(user_messages) if message.get('id') is not None
    }
    bound_contexts = []

    for context in contexts or []:
        bound_context = copy.deepcopy(context)
        message_id = context.get('message_id')
        if message_id is not None:
            user_message_index = message_indices.get(message_id)
            if (
                user_message_index is None
                and not message_indices
                and context.get('user_message_count') == len(user_messages)
            ):
                user_message_index = context.get('user_message_index')
        elif context.get('user_message_count') == len(user_messages):
            user_message_index = context.get('user_message_index')
        else:
            user_message_index = None

        if isinstance(user_message_index, int) and 0 <= user_message_index < len(user_messages):
            bound_context['user_message_index'] = user_message_index
            bound_context['content'] = copy.deepcopy(user_messages[user_message_index].get('content', ''))
        else:
            bound_context['user_message_index'] = None
            bound_context.pop('content', None)

        bound_contexts.append(bound_context)

    return bound_contexts


def exclude_message_scoped_files(files: list[dict], contexts: list[dict]) -> list[dict]:
    message_file_keys = {
        get_file_context_item_key(file) for context in contexts or [] for file in context.get('files', [])
    }
    return [file for file in files or [] if get_file_context_item_key(file) not in message_file_keys]


def restore_user_message_contents(messages: list[dict], contents: list[Any]) -> list[dict]:
    for message, content in zip(get_user_messages(messages), contents or []):
        message['content'] = copy.deepcopy(content)
    return messages
