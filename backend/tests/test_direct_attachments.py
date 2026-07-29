from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.utils import direct_attachments, middleware
from open_webui.utils.direct_attachments import (
    DIRECT_ATTACHMENT_PURPOSE,
    clear_direct_attachment_contexts,
    collect_direct_attachment_contexts,
    hydrate_direct_attachments,
    is_direct_attachment,
)


def direct_file(**overrides):
    return {
        'type': 'file',
        'id': 'file-one',
        'name': 'notes.txt',
        'purpose': DIRECT_ATTACHMENT_PURPOSE,
        **overrides,
    }


def test_direct_attachment_is_explicit_and_excludes_images():
    assert is_direct_attachment(direct_file())
    assert is_direct_attachment(direct_file(type='text', content='inline'))

    assert not is_direct_attachment({'type': 'file', 'id': 'knowledge-file'})
    assert not is_direct_attachment(direct_file(content_type='image/png'))
    assert not is_direct_attachment('not-a-file')


def test_collects_and_deduplicates_files_on_the_owning_user_message():
    source = direct_file()
    messages = [
        {'role': 'system', 'content': 'system'},
        {'id': 'user-one', 'role': 'user', 'content': 'First', 'files': [source, source]},
        {'role': 'assistant', 'content': 'Answer'},
        {
            'id': 'user-two',
            'role': 'user',
            'content': 'Second',
            'files': [direct_file(id='file-two', name='second.txt')],
        },
    ]

    contexts = collect_direct_attachment_contexts(messages)

    assert contexts == [
        {
            'message_id': 'user-one',
            'user_message_index': 0,
            'user_message_count': 2,
            'files': [source],
        },
        {
            'message_id': 'user-two',
            'user_message_index': 1,
            'user_message_count': 2,
            'files': [direct_file(id='file-two', name='second.txt')],
        },
    ]
    assert contexts[0]['files'][0] is not source


def test_collection_follows_surviving_and_reordered_messages():
    first = {
        'id': 'user-one',
        'role': 'user',
        'content': 'First',
        'files': [direct_file(id='file-one')],
    }
    second = {
        'id': 'user-two',
        'role': 'user',
        'content': 'Second',
        'files': [direct_file(id='file-two')],
    }

    assert collect_direct_attachment_contexts([second, first]) == [
        {
            'message_id': 'user-two',
            'user_message_index': 0,
            'user_message_count': 2,
            'files': [direct_file(id='file-two')],
        },
        {
            'message_id': 'user-one',
            'user_message_index': 1,
            'user_message_count': 2,
            'files': [direct_file(id='file-one')],
        },
    ]
    assert collect_direct_attachment_contexts([second]) == [
        {
            'message_id': 'user-two',
            'user_message_index': 0,
            'user_message_count': 1,
            'files': [direct_file(id='file-two')],
        }
    ]


def test_filter_file_ownership_clears_contexts_and_message_file_fields():
    form_data = {
        'messages': [{'role': 'user', 'content': 'Prompt', 'files': [direct_file()]}],
        'files': [{'type': 'collection', 'id': 'knowledge'}],
        'metadata': {
            'chat_id': 'chat-one',
            'direct_attachment_contexts': [{'message_id': 'user-one'}],
        },
    }

    clear_direct_attachment_contexts(form_data)

    assert form_data == {
        'messages': [{'role': 'user', 'content': 'Prompt'}],
        'files': [{'type': 'collection', 'id': 'knowledge'}],
        'metadata': {'chat_id': 'chat-one'},
    }


@pytest.mark.asyncio
async def test_hydrates_saved_and_temporary_files_on_their_exact_messages(monkeypatch):
    saved_file = SimpleNamespace(
        id='file-one',
        user_id='user-one',
        filename='stored-name.txt',
        data={'status': 'completed', 'content': 'saved body'},
    )
    get_file = AsyncMock(return_value=saved_file)
    access = AsyncMock(return_value=False)
    monkeypatch.setattr(direct_attachments.Files, 'get_file_by_id', get_file)
    monkeypatch.setattr(direct_attachments, 'has_access_to_file', access)

    messages = [
        {'role': 'user', 'content': 'Read saved'},
        {'role': 'assistant', 'content': 'Answer'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Read inline'}]},
    ]
    contexts = [
        {
            'user_message_index': 0,
            'files': [direct_file(), direct_file()],
        },
        {
            'user_message_index': 1,
            'files': [
                direct_file(
                    type='text',
                    id='temporary-one',
                    name='inline<&>.txt',
                    content='inline body',
                )
            ],
        },
    ]

    await hydrate_direct_attachments(
        messages,
        contexts,
        SimpleNamespace(id='user-one', role='user'),
    )

    assert messages[0]['content'] == (
        'Read saved\n\n<attached_files>\n'
        '<attached_file name="notes.txt">\nsaved body\n</attached_file>\n'
        '</attached_files>'
    )
    assert messages[2]['content'][0]['text'] == (
        'Read inline\n\n<attached_files>\n'
        '<attached_file name="inline&lt;&amp;&gt;.txt">\ninline body\n</attached_file>\n'
        '</attached_files>'
    )
    get_file.assert_awaited_once_with('file-one')
    access.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_inaccessible_failed_and_missing_content(monkeypatch):
    records = {
        'foreign': SimpleNamespace(
            id='foreign',
            user_id='other-user',
            filename='foreign.txt',
            data={'status': 'completed', 'content': 'private'},
        ),
        'failed': SimpleNamespace(
            id='failed',
            user_id='user-one',
            filename='failed.txt',
            data={'status': 'failed', 'content': 'partial'},
        ),
        'empty': SimpleNamespace(
            id='empty',
            user_id='user-one',
            filename='empty.txt',
            data={'status': 'completed'},
        ),
    }

    monkeypatch.setattr(
        direct_attachments.Files,
        'get_file_by_id',
        AsyncMock(side_effect=lambda file_id: records.get(file_id)),
    )
    monkeypatch.setattr(direct_attachments, 'has_access_to_file', AsyncMock(return_value=False))

    messages = [{'role': 'user', 'content': 'Prompt'}]
    contexts = [
        {
            'user_message_index': 0,
            'files': [
                direct_file(id='foreign'),
                direct_file(id='failed'),
                direct_file(id='empty'),
                direct_file(id='missing'),
            ],
        }
    ]

    await hydrate_direct_attachments(
        messages,
        contexts,
        SimpleNamespace(id='user-one', role='user'),
    )

    assert messages == [{'role': 'user', 'content': 'Prompt'}]


@pytest.mark.asyncio
async def test_native_file_context_does_not_duplicate_direct_attachments(monkeypatch):
    direct = direct_file(url='file-one')
    knowledge = {
        'type': 'file',
        'id': 'knowledge-file',
        'url': 'knowledge-file',
        'name': 'knowledge.txt',
    }
    stored_user = {
        'id': 'user-one',
        'parentId': None,
        'childrenIds': ['assistant-one'],
        'role': 'user',
        'content': 'Read both',
        'files': [direct, knowledge],
    }
    stored_assistant = {
        'id': 'assistant-one',
        'parentId': 'user-one',
        'childrenIds': [],
        'role': 'assistant',
        'content': 'Answer',
    }
    chat = SimpleNamespace(
        chat={
            'history': {
                'currentId': 'assistant-one',
                'messages': {
                    'user-one': stored_user,
                    'assistant-one': stored_assistant,
                },
            }
        }
    )
    monkeypatch.setattr(middleware.Chats, 'get_chat_by_id_and_user_id', AsyncMock(return_value=chat))

    messages = [
        {
            'role': 'user',
            'content': (
                'Read both\n\n<attached_files>\n'
                '<attached_file name="notes.txt">\ndirect body\n</attached_file>\n'
                '</attached_files>'
            ),
        }
    ]

    result = await middleware.add_file_context(
        messages,
        'chat-one',
        SimpleNamespace(id='user-one', role='user'),
    )

    assert '<file type="file" id="knowledge-file" url="knowledge-file" name="knowledge.txt"/>' in result[0]['content']
    assert '<file type="file" id="file-one"' not in result[0]['content']
    assert result[0]['content'].count('notes.txt') == 1
