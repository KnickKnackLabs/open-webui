from open_webui.utils.message_file_context import (
    bind_message_file_contexts,
    clear_file_context_payload,
    collect_message_file_contexts,
    exclude_message_scoped_files,
    get_file_context_item_key,
    is_document_context_file_item,
    merge_message_file_contexts,
    restore_user_message_contents,
)


def test_document_context_excludes_images():
    assert is_document_context_file_item({'type': 'file', 'id': 'text-file'})
    assert is_document_context_file_item({'type': 'collection', 'collection_names': ['notes']})
    assert is_document_context_file_item({'type': 'custom', 'docs': ['inline']})

    assert not is_document_context_file_item({'type': 'image', 'id': 'image-file'})
    assert not is_document_context_file_item({'type': 'file', 'content_type': 'image/png'})
    assert not is_document_context_file_item('not-a-file')


def test_collects_files_on_the_user_message_that_introduced_them():
    source_file = {'type': 'file', 'id': 'session-one', 'name': 'session-one.txt'}
    messages = [
        {'role': 'system', 'content': 'system'},
        {'id': 'user-one', 'role': 'user', 'content': 'First', 'files': [source_file, source_file]},
        {'role': 'assistant', 'content': 'Answer'},
        {
            'id': 'user-two',
            'role': 'user',
            'content': 'Second',
            'files': [{'type': 'image', 'id': 'image-one'}],
        },
    ]

    contexts = collect_message_file_contexts(messages)

    assert contexts == [
        {
            'message_id': 'user-one',
            'user_message_index': 0,
            'user_message_count': 2,
            'files': [source_file],
        }
    ]
    assert contexts[0]['files'][0] is not source_file


def test_preserves_current_request_files_when_db_reload_has_none():
    request_contexts = collect_message_file_contexts(
        [
            {
                'id': 'current-user',
                'role': 'user',
                'content': 'Read this',
                'files': [
                    {'type': 'file', 'id': 'source-file', 'name': 'request-name.txt'},
                    {'type': 'file', 'id': 'source-file', 'name': 'request-name.txt'},
                ],
            }
        ]
    )
    db_messages = [{'id': 'current-user', 'role': 'user', 'content': 'Read this'}]

    merged = merge_message_file_contexts(
        request_contexts,
        collect_message_file_contexts(db_messages),
    )

    assert bind_message_file_contexts(merged, db_messages) == [
        {
            'message_id': 'current-user',
            'user_message_index': 0,
            'user_message_count': 1,
            'content': 'Read this',
            'files': [{'type': 'file', 'id': 'source-file', 'name': 'request-name.txt'}],
        }
    ]


def test_merges_db_and_request_files_for_the_same_message():
    request_contexts = collect_message_file_contexts(
        [
            {
                'id': 'current-user',
                'role': 'user',
                'content': 'Read these',
                'files': [{'type': 'file', 'id': 'request-file'}],
            }
        ]
    )
    db_contexts = collect_message_file_contexts(
        [
            {
                'id': 'current-user',
                'role': 'user',
                'content': 'Read these',
                'files': [{'type': 'file', 'id': 'db-file'}],
            }
        ]
    )

    assert merge_message_file_contexts(request_contexts, db_contexts)[0]['files'] == [
        {'type': 'file', 'id': 'request-file'},
        {'type': 'file', 'id': 'db-file'},
    ]


def test_rebinds_surviving_file_context_after_history_compaction():
    contexts = collect_message_file_contexts(
        [
            {
                'id': 'dropped-user',
                'role': 'user',
                'content': 'Dropped',
                'files': [{'type': 'file', 'id': 'dropped-file'}],
            },
            {'role': 'assistant', 'content': 'Answer'},
            {
                'id': 'kept-user',
                'role': 'user',
                'content': 'Original',
                'files': [{'type': 'file', 'id': 'kept-file'}],
            },
        ]
    )
    compacted_messages = [
        {'role': 'system', 'content': 'Summary'},
        {'id': 'kept-user', 'role': 'user', 'content': 'Current'},
    ]

    bound_contexts = bind_message_file_contexts(contexts, compacted_messages)

    assert bound_contexts == [
        {
            'message_id': 'dropped-user',
            'user_message_index': None,
            'user_message_count': 2,
            'files': [{'type': 'file', 'id': 'dropped-file'}],
        },
        {
            'message_id': 'kept-user',
            'user_message_index': 0,
            'user_message_count': 2,
            'content': 'Current',
            'files': [{'type': 'file', 'id': 'kept-file'}],
        },
    ]
    assert (
        exclude_message_scoped_files(
            [{'type': 'file', 'id': 'dropped-file'}, {'type': 'file', 'id': 'kept-file'}],
            bound_contexts,
        )
        == []
    )


def test_rebinds_by_position_after_provider_normalization_strips_message_ids():
    contexts = collect_message_file_contexts(
        [
            {
                'id': 'user-one',
                'role': 'user',
                'content': 'Original',
                'files': [{'type': 'file', 'id': 'source-file'}],
            }
        ]
    )

    assert bind_message_file_contexts(contexts, [{'role': 'user', 'content': 'Normalized'}]) == [
        {
            'message_id': 'user-one',
            'user_message_index': 0,
            'user_message_count': 1,
            'content': 'Normalized',
            'files': [{'type': 'file', 'id': 'source-file'}],
        }
    ]


def test_does_not_rebind_an_idless_file_after_compaction():
    contexts = collect_message_file_contexts(
        [
            {
                'role': 'user',
                'content': 'Original',
                'files': [{'type': 'file', 'id': 'source-file'}],
            },
            {'role': 'assistant', 'content': 'Answer'},
            {'role': 'user', 'content': 'Current'},
        ]
    )

    assert bind_message_file_contexts(contexts, [{'role': 'user', 'content': 'Current'}]) == [
        {
            'message_id': None,
            'user_message_index': None,
            'user_message_count': 2,
            'files': [{'type': 'file', 'id': 'source-file'}],
        }
    ]


def test_excludes_message_scoped_files_from_global_context_by_identity():
    message_file = {'type': 'file', 'id': 'session-one', 'name': 'old-name.txt'}
    global_files = [
        {'type': 'file', 'id': 'session-one', 'name': 'new-name.txt'},
        {'type': 'collection', 'collection_name': 'knowledge'},
    ]
    contexts = [{'files': [message_file]}]

    assert get_file_context_item_key(message_file) == 'file:id:session-one'
    assert exclude_message_scoped_files(global_files, contexts) == [
        {'type': 'collection', 'collection_name': 'knowledge'}
    ]


def test_filter_file_ownership_clears_all_default_file_context():
    form_data = {
        'files': [{'type': 'file', 'id': 'source-file'}],
        'metadata': {
            'files': [{'type': 'file', 'id': 'source-file'}],
            'message_file_contexts': [{'message_id': 'user-one'}],
            'chat_id': 'chat-one',
        },
    }

    assert clear_file_context_payload(form_data) == {'metadata': {'chat_id': 'chat-one'}}


def test_restores_all_user_message_contents_without_aliasing():
    messages = [
        {'role': 'user', 'content': 'mutated one'},
        {'role': 'assistant', 'content': 'answer'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'mutated two'}]},
    ]
    original_contents = ['original one', [{'type': 'text', 'text': 'original two'}]]

    restore_user_message_contents(messages, original_contents)
    original_contents[1][0]['text'] = 'changed after restore'

    assert messages[0]['content'] == 'original one'
    assert messages[2]['content'] == [{'type': 'text', 'text': 'original two'}]
