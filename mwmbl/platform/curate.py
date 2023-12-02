from logging import getLogger
from typing import Any, Optional
from urllib.parse import parse_qs

from ninja import Router, NinjaAPI

from mwmbl.indexer.update_urls import get_datetime_from_timestamp
from mwmbl.models import UserCuration
from mwmbl.platform.data import CurateBegin, CurateMove, CurateDelete, CurateAdd, CurateValidate, \
    make_curation_type
from mwmbl.tinysearchengine.indexer import TinyIndex, Document, DocumentState
from mwmbl.tokenizer import tokenize
from mwmbl.utils import add_term_info, add_term_infos

RESULT_URL = "https://mwmbl.org/?q="
MAX_CURATED_SCORE = 1_111_111.0


logger = getLogger(__name__)


def create_router(index_path: str, version: str) -> NinjaAPI:
    router = NinjaAPI(urls_namespace=f"curate-{version}", csrf=True)

    @router.post("/begin")
    def user_begin_curate(request, curate_begin: make_curation_type(CurateBegin)):
        return _curate(request, "curate_begin", curate_begin)

    @router.post("/move")
    def user_move_result(request, curate_move: make_curation_type(CurateMove)):
        return _curate(request, "curate_move", curate_move)

    @router.post("/delete")
    def user_delete_result(request, curate_delete: make_curation_type(CurateDelete)):
        return _curate(request, "curate_delete", curate_delete)

    @router.post("/add")
    def user_add_result(request, curate_add: make_curation_type(CurateAdd)):
        return _curate(request, "curate_add", curate_add)

    @router.post("/validate")
    def user_add_result(request, curate_validate: make_curation_type(CurateValidate)):
        return _curate(request, "curate_validate", curate_validate)

    def _curate(request, curation_type: str, curation: Any):
        user_curation = UserCuration(
            user=request.user,
            timestamp=get_datetime_from_timestamp(curation.timestamp / 1000.0),
            url=curation.url,
            results=curation.dict()["results"],
            curation_type=curation_type,
            curation=curation.curation.dict(),
        )
        user_curation.save()

        with TinyIndex(Document, index_path, 'w') as indexer:
            query_string = parse_qs(curation.url)
            if len(query_string) > 1:
                raise ValueError(f"Should be one query string in the URL: {curation.url}")

            queries = next(iter(query_string.values()))
            if len(queries) > 1:
                raise ValueError(f"Should be one query value in the URL: {curation.url}")

            query = queries[0]
            tokens = tokenize(query)
            term = " ".join(tokens)

            documents = [
                Document(
                    title=result.title,
                    url=result.url,
                    extract=result.extract,
                    score=MAX_CURATED_SCORE - i,
                    term=term,
                    state=_get_document_state(result.validated, result.source),
                )
                for i, result in enumerate(curation.results)
            ]

            page_index = indexer.get_key_page_index(term)
            existing_documents_no_terms = indexer.get_page(page_index)
            existing_documents = add_term_infos(existing_documents_no_terms, indexer, page_index)
            other_documents = [doc for doc in existing_documents if doc.term != term]
            logger.info(f"Found {len(other_documents)} other documents for term {term} at page {page_index} "
                        f"with terms { {doc.term for doc in other_documents} }")

            all_documents = documents + other_documents
            logger.info(f"Storing {len(all_documents)} documents at page {page_index}")
            indexer.store_in_page(page_index, all_documents)

        return {"curation": "ok"}

    return router


def _get_document_state(validated: bool, source: str) -> Optional[DocumentState]:
    if validated:
        if source.lower() == "user":
            return DocumentState.FROM_USER_APPROVED
        elif source.lower() == "google":
            return DocumentState.FROM_GOOGLE_APPROVED
        else:
            return DocumentState.ORGANIC_APPROVED
    elif source.lower() == "user":
        return DocumentState.FROM_USER
    elif source.lower() == "google":
        return DocumentState.FROM_GOOGLE
    else:
        return None
