from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.routers import retrieval
from open_webui.routers.files import _is_direct_attachment_upload
from open_webui.routers.retrieval import ProcessFileForm
from open_webui.utils.direct_attachments import DIRECT_ATTACHMENT_PURPOSE
from open_webui.utils.misc import calculate_sha256_string


def test_only_ordinary_chat_attachments_select_extraction_only():
    assert _is_direct_attachment_upload({'purpose': DIRECT_ATTACHMENT_PURPOSE})
    assert not _is_direct_attachment_upload({})
    assert not _is_direct_attachment_upload(
        {
            'purpose': DIRECT_ATTACHMENT_PURPOSE,
            'knowledge_id': 'knowledge-one',
        }
    )


@pytest.mark.asyncio
async def test_extract_only_stores_content_without_loading_or_writing_vectors(monkeypatch):
    file = SimpleNamespace(
        id='file-one',
        user_id='user-one',
        filename='notes.txt',
        path=None,
        data={},
        meta={'content_type': 'text/plain'},
    )

    get_file = AsyncMock(return_value=file)
    update_data = AsyncMock()
    update_hash = AsyncMock()
    delete_collection = AsyncMock(side_effect=AssertionError('vector delete must not run'))
    get_retrieval_config = AsyncMock(side_effect=AssertionError('retrieval config must not load'))

    monkeypatch.setattr(retrieval.Files, 'get_file_by_id_and_user_id', get_file)
    monkeypatch.setattr(retrieval.Files, 'update_file_data_by_id', update_data)
    monkeypatch.setattr(retrieval.Files, 'update_file_hash_by_id', update_hash)
    monkeypatch.setattr(retrieval.ASYNC_VECTOR_DB_CLIENT, 'delete_collection', delete_collection)
    monkeypatch.setattr(retrieval, 'get_retrieval_config', get_retrieval_config)

    db = SimpleNamespace()
    result = await retrieval.process_file(
        SimpleNamespace(),
        ProcessFileForm(
            file_id='file-one',
            content='transcript body',
            extract_only=True,
        ),
        user=SimpleNamespace(id='user-one', role='user'),
        db=db,
    )

    assert result == {
        'status': True,
        'collection_name': None,
        'filename': 'notes.txt',
        'content': 'transcript body',
    }
    get_file.assert_awaited_once()
    assert update_data.await_args_list[0].args == ('file-one', {'content': 'transcript body'})
    assert update_data.await_args_list[1].args == ('file-one', {'status': 'completed'})
    update_hash.assert_awaited_once_with(
        'file-one',
        calculate_sha256_string('transcript body'),
        db=db,
    )
    delete_collection.assert_not_awaited()
    get_retrieval_config.assert_not_awaited()
