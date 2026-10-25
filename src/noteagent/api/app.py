"""FastAPI application factory for the local wiki workbench.

The server is the product's one origin: it answers ``/api/*`` and serves the built
interface at ``/``. Everything the interface asks for is relative to ``/api``, so
there is no configuration step and no cross-origin request.

``/api`` is not a stable contract. It changes in the same commit as the interface
that uses it: there is no version negotiation, and no compatibility with a client
built earlier.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from noteagent import __version__
from noteagent.acquire.intake.local_file import MAX_LOCAL_FILES
from noteagent.acquire.search_config import SearchConfigError, SearchError
from noteagent.acquire.tools.fetchers.html import MAX_HTML_BYTES, MAX_PDF_BYTES
from noteagent.acquire.types import ProviderNotConfiguredError
from noteagent.api.attention import attention_payload
from noteagent.api.chat import (
    delete_conversation_payload,
    get_conversation_payload,
    get_limits_payload,
    iter_chat_query_sse,
    list_chats_payload,
    post_conversation_payload,
    put_conversation_payload,
    put_limits_payload,
    rename_conversation_payload,
)
from noteagent.api.clips import get_clips_payload, put_clips_payload
from noteagent.api.deep_search import iter_act_sse
from noteagent.api.diagnostics import (
    MAX_TAIL_LINES,
    check_update,
    diagnostics_payload,
    log_payload,
)
from noteagent.api.errors import (
    CodedHTTPException,
    coded_error_handler,
    http_error,
    refusal_handler,
)
from noteagent.api.fetch import run_fetch_payload, run_local_payload, run_rendered_payload
from noteagent.api.files import (
    delete_project_file,
    list_project_tree,
    read_project_bytes,
    read_project_text,
)
from noteagent.api.guard import LocalOnly
from noteagent.api.ingest import (
    ensure_llm_configured,
    iter_ingest_sse,
    resolve_http_ingest_paths,
)
from noteagent.api.notes import (
    create_note_payload,
    delete_note_payload,
    get_note_payload,
    list_notes_payload,
    update_note_payload,
)
from noteagent.api.pages import (
    page_backlinks_payload,
    page_history_payload,
    page_rename_payload,
    page_restore_payload,
    page_save_payload,
    page_version_payload,
    page_view_payload,
    project_search_payload,
)
from noteagent.api.projects import (
    create_project,
    delete_project,
    export_project_archive,
    get_project_layout,
    init_project_named,
    inspect_folder,
    list_projects,
    open_existing_folder,
    projects_dir,
    require_project_layout,
    reset_project,
    resolve_repo_root,
    use_project,
)
from noteagent.api.save import capture_from_api, run_save_payload
from noteagent.api.search import run_search_payload
from noteagent.api.topic_gate import iter_ingest_confirmed_sse, iter_topic_map_sse
from noteagent.api.web_static import BuiltInterface, resolve_web_dist
from noteagent.ingest.ingest_recovery import discard_recovery_job, recovery_payload
from noteagent.ingest.revision_inbox import (
    acknowledge_deleted,
    ignore_inbox_item,
    note_source_change,
    poll_active_inbox,
    revision_inbox_payload,
)
from noteagent.ingest.source_citations import source_citations_payload
from noteagent.ingest.update_index import rebuild_wiki_index
from noteagent.llm.config import LlmConfigError
from noteagent.settings.brief import brief_view, save_brief
from noteagent.settings.install import apply_install, install_view, probe_llm, probe_search
from noteagent.project.lifecycle import ProjectConfirmError, project_contents
from noteagent.project.names import ProjectNameError
from noteagent.project.trash import list_trash, restore_trash
from noteagent.util.refusal import CodedError
from noteagent.wiki.deep_search import (
    act_session as act_deep_search,
    get_session_payload as get_deep_search_payload,
    put_session_payload as put_deep_search_payload,
)
from noteagent.wiki.graph import wiki_graph_payload


def _project_http_error(err: Exception) -> HTTPException:
    """Status for a project lifecycle failure; anything unnamed is our fault."""
    if isinstance(err, (ProjectNameError, ProjectConfirmError)):
        return http_error(err, 400)
    if isinstance(err, FileExistsError):
        return HTTPException(status_code=409, detail=str(err))
    if isinstance(err, FileNotFoundError):
        return http_error(err, 404)
    if isinstance(err, ValueError):
        return http_error(err, 400)
    return HTTPException(status_code=500, detail=str(err))


def _settle_restored(restored: object) -> None:
    """Make the rest of the project agree with what just came back.

    A restored wiki page is not in the index, and a restored source has not been
    read again: both are marked the same way a removal marks them. A restored
    project is complete as it was, so nothing inside it has changed.
    """
    if not isinstance(restored, dict):
        return
    kind = str(restored.get("kind") or "")
    project = str(restored.get("project") or "")
    if not project or kind == "project":
        return
    try:
        layout = get_project_layout(project)
        relative = str(restored.get("relativePath") or "")
        if kind == "file" and relative.startswith("wiki/"):
            rebuild_wiki_index(layout)
        note_source_change(layout)
    except Exception:
        # The bytes are back, which is what was asked for; a project that no
        # longer opens is reported by the next request that needs it.
        return


class ProjectUseBody(BaseModel):
    name: str = Field(..., min_length=1)


class ProjectCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class ProjectConfirmBody(BaseModel):
    confirmName: str = Field(..., min_length=1, max_length=64)


class ProjectResetBody(BaseModel):
    confirmName: str = ""
    dryRun: bool = False


class FolderOpenBody(BaseModel):
    """A folder to open where it is, or to copy in as a new project."""

    path: str = Field(..., min_length=1, max_length=4096)
    name: str = Field(default="", max_length=64)
    mode: str = Field(default="link", pattern="^(link|copy)$")


class LlmSettingsBody(BaseModel):
    baseUrl: str | None = None
    apiKey: str | None = None
    clearApiKey: bool = False
    model: str | None = None
    temperature: float | None = None
    maxTokens: int | None = None
    timeoutSeconds: float | None = None


class SearchSettingsBody(BaseModel):
    provider: str | None = None
    apiKey: str | None = None
    clearApiKey: bool = False
    timeoutSeconds: float | None = None
    country: str | None = None
    searchLang: str | None = None
    uiLang: str | None = None


class InstallSettingsBody(BaseModel):
    llm: LlmSettingsBody | None = None
    search: SearchSettingsBody | None = None
    extractMedia: bool | None = None


class BriefBody(BaseModel):
    text: str = Field(default="", max_length=100_000)


class AcquireSearchBody(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=20)


class AcquireFetchBody(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=8)
    query: str = ""


class AcquireRenderedBody(BaseModel):
    url: str = Field(..., min_length=1)
    html: str = Field(..., min_length=1, max_length=MAX_HTML_BYTES)


#: Base64 of the upload ceiling, plus room for the encoding's own overhead.
_MAX_UPLOAD_B64 = (MAX_PDF_BYTES * 4) // 3 + 64


class AcquireLocalFile(BaseModel):
    name: str = Field(..., min_length=1, max_length=240)
    mediaType: str = ""
    contentBase64: str = Field(..., min_length=1, max_length=_MAX_UPLOAD_B64)


class AcquireLocalBody(BaseModel):
    files: list[AcquireLocalFile] = Field(
        ...,
        min_length=1,
        max_length=MAX_LOCAL_FILES,
    )


class AcquireSaveCapture(BaseModel):
    url: str = Field(..., min_length=1)
    markdown: str = ""
    html: str = ""
    title: str = ""
    captureMethod: str = "trafilatura-html"
    fidelity: str = ""
    reasons: list[str] = Field(default_factory=list)
    httpStatus: int | None = None
    finalUrl: str = ""
    charCount: int = 0
    imageCount: int = 0
    imagesFailed: int = 0
    kind: str = ""
    pdfBase64: str = Field(default="", max_length=_MAX_UPLOAD_B64)
    officeBase64: str = Field(default="", max_length=_MAX_UPLOAD_B64)
    imageBase64: str = Field(default="", max_length=_MAX_UPLOAD_B64)
    pageCount: int = 0


class AcquireSaveBody(BaseModel):
    captures: list[AcquireSaveCapture] = Field(..., min_length=1, max_length=8)


class ProjectFileDeleteBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)


class RevisionInboxIgnoreBody(BaseModel):
    identity: str = Field(..., min_length=1, max_length=500)
    kind: str = Field(..., min_length=1, max_length=40)


class RevisionInboxAckBody(BaseModel):
    identity: str = Field(..., min_length=1, max_length=500)


class IngestRecoveryDiscardBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)


class IngestBody(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=8)
    force: bool = False


class ClipBlockBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=40)
    tag: str = ""
    text: str = ""
    preview: str = ""


class ClipCollectionBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    sourceKey: str = Field(..., min_length=1, max_length=500)
    sourceLabel: str = ""
    blockIds: list[str] = Field(..., min_length=1, max_length=200)
    blocks: list[ClipBlockBody] = Field(default_factory=list)
    roundId: str = ""
    targetSlug: str = ""


class ChatMessageBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    role: str = Field(..., min_length=1, max_length=16)
    content: str = ""
    createdAt: str = ""


class ChatPutBody(BaseModel):
    title: str = ""
    messages: list[ChatMessageBody] = Field(default_factory=list, max_length=2000)


class ChatCreateBody(BaseModel):
    title: str = ""


class ChatRenameBody(BaseModel):
    title: str = Field(default="", max_length=2_000)


class NoteCreateBody(BaseModel):
    page: str = Field(..., min_length=1, max_length=500)
    body: str = ""
    anchor: str = ""
    quote: str = ""


class NoteUpdateBody(BaseModel):
    """A field left out keeps what the note already had."""

    body: str | None = None
    anchor: str | None = None
    quote: str | None = None


class PageSaveBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    body: str = Field(default="", max_length=2_000_000)
    #: The revision the editor opened, so a page written since is not overwritten.
    baseRevision: str = ""
    #: Say what would change rather than change it.
    preview: bool = False


class PageRenameBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    to: str = Field(..., min_length=1, max_length=500)


class PageRestoreBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    id: str = Field(..., min_length=1, max_length=80)
    baseRevision: str = ""


class ChatLimitsBody(BaseModel):
    historyLimit: int = Field(..., ge=1, le=200)
    maxMessages: int = Field(..., ge=1, le=2000)
    maxContentChars: int = Field(..., ge=200, le=100_000)
    maxConversations: int = Field(..., ge=1, le=200)
    maxTitleChars: int = Field(..., ge=16, le=240)
    maxBriefChars: int = Field(default=6_000, ge=0, le=40_000)
    maxIndexChars: int = Field(default=12_000, ge=200, le=80_000)
    maxOpenPages: int = Field(default=6, ge=1, le=30)
    maxPageChars: int = Field(default=8_000, ge=200, le=40_000)
    maxPagesChars: int = Field(default=24_000, ge=200, le=120_000)
    maxToolRounds: int = Field(default=6, ge=1, le=20)


class ChatQueryBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=100_000)


class GateDecisionBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=160)
    action: str = "skip"
    sectionIds: list[str] = Field(default_factory=list, max_length=40)


class ClipsPutBody(BaseModel):
    intent: str = ""
    collections: list[ClipCollectionBody] = Field(default_factory=list, max_length=40)
    topicMap: dict[str, object] | None = None
    decisions: list[GateDecisionBody] | None = None
    #: The board's answers, which a later plan carries over. Separate from the gate
    #: above, which only ever holds the pass that was confirmed.
    choices: list[GateDecisionBody] | None = None


class TopicMapBody(BaseModel):
    intent: str = ""
    sourceIdentity: str = Field(default="", max_length=500)
    #: The language the reader is using. Explanations are written for them, so they
    #: follow this; page names follow the material.
    language: str = Field(default="", max_length=20)


class ConfirmedPageBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str = ""
    wikiPath: str = ""
    action: str = "skip"
    sectionIds: list[str] = Field(default_factory=list)


class IngestConfirmedBody(BaseModel):
    intent: str = ""
    confirmed: list[ConfirmedPageBody] = Field(..., min_length=1, max_length=20)


class DeepSearchActBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=40)
    mode: str = ""
    intent: str = ""
    seed: dict[str, object] | None = None
    slug: str = ""
    locator: str = ""
    url: str = ""
    accepted: bool | None = None
    picked: bool | None = None
    leftover: str = ""
    step: int | None = Field(default=None, ge=2, le=5)
    collectionIds: list[str] | None = None
    useLlm: bool = True
    #: The language the reader is using. A plan is read by them, so it is written in
    #: this; page names follow the material.
    language: str = Field(default="", max_length=20)


class DeepSearchPutBody(BaseModel):
    clear: bool = False
    action: str = ""
    mode: str = ""
    intent: str = ""
    seed: dict[str, object] | None = None
    slug: str = ""
    locator: str = ""
    url: str = ""
    accepted: bool | None = None
    picked: bool | None = None
    leftover: str = ""
    step: int | None = Field(default=None, ge=2, le=5)
    collectionIds: list[str] | None = None


def _captures_from_body(body: AcquireSaveBody):
    return [
        capture_from_api(
            url=item.url,
            markdown=item.markdown,
            html=item.html,
            title=item.title,
            capture_method=item.captureMethod,
            fidelity=item.fidelity,
            reasons=item.reasons,
            http_status=item.httpStatus,
            final_url=item.finalUrl,
            char_count=item.charCount,
            image_count=item.imageCount,
            images_failed=item.imagesFailed,
            kind=item.kind,
            pdf_base64=item.pdfBase64,
            office_base64=item.officeBase64,
            image_base64=item.imageBase64,
            page_count=item.pageCount,
        )
        for item in body.captures
    ]


_INBOX_POLL_S = 8.0


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    stop = asyncio.Event()

    async def _poll_inbox() -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=_INBOX_POLL_S)
                return
            except asyncio.TimeoutError:
                await asyncio.to_thread(poll_active_inbox)

    task = asyncio.create_task(_poll_inbox())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    app = FastAPI(
        title="NOTEAGENT API",
        version=__version__,
        lifespan=_app_lifespan,
    )
    # There is no password, so the headers a browser fills in are what says whether
    # a request was meant for this server at all.
    app.add_middleware(LocalOnly)

    # A refusal raised where no route caught it still reaches the interface with
    # the situation it named, rather than as a failure of the service.
    app.add_exception_handler(CodedError, refusal_handler)
    app.add_exception_handler(CodedHTTPException, coded_error_handler)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Reading only: the screen these answer is the one a person opens when the app
    # is already misbehaving, so nothing here changes the state it describes.
    @app.get("/api/diagnostics")
    def diagnostics() -> dict[str, object]:
        return diagnostics_payload()

    @app.get("/api/diagnostics/log")
    def diagnostics_log(
        lines: int = Query(80, ge=1, le=MAX_TAIL_LINES),
    ) -> dict[str, object]:
        return log_payload(lines=lines)

    @app.post("/api/diagnostics/update")
    def diagnostics_update() -> dict[str, object]:
        return check_update()

    @app.get("/api/settings")
    def settings_get() -> dict[str, object]:
        return install_view()

    @app.put("/api/settings")
    def settings_put(body: InstallSettingsBody) -> dict[str, object]:
        return apply_install(body.model_dump(exclude_unset=True))

    @app.post("/api/settings/llm/probe")
    def settings_llm_probe(body: LlmSettingsBody) -> dict[str, object]:
        probe_llm(body.model_dump(exclude_unset=True))
        return {"ok": True}

    @app.post("/api/settings/search/probe")
    def settings_search_probe(body: SearchSettingsBody) -> dict[str, object]:
        count = probe_search(body.model_dump(exclude_unset=True))
        return {"ok": True, "count": count}

    @app.get("/api/projects")
    def projects() -> dict[str, object]:
        return {"projects": list_projects()}

    @app.post("/api/projects/use")
    def projects_use(body: ProjectUseBody) -> dict[str, object]:
        try:
            return use_project(body.name)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err

    @app.post("/api/projects")
    def projects_create(body: ProjectCreateBody) -> dict[str, object]:
        try:
            return create_project(body.name)
        except Exception as err:
            raise _project_http_error(err) from err

    @app.get("/api/folders/inspect")
    def folders_inspect(path: str = Query(..., description="An absolute folder path")) -> dict:
        """What an existing folder holds, before it is opened or copied in."""
        return inspect_folder(path)

    @app.post("/api/folders/open")
    def folders_open(body: FolderOpenBody) -> dict[str, object]:
        try:
            return open_existing_folder(body.path, name=body.name, mode=body.mode)
        except Exception as err:
            raise _project_http_error(err) from err

    @app.post("/api/projects/{name}/init")
    def projects_init(name: str) -> dict[str, object]:
        try:
            return init_project_named(name)
        except Exception as err:
            raise _project_http_error(err) from err

    @app.post("/api/projects/{name}/reset")
    def projects_reset(name: str, body: ProjectResetBody) -> dict[str, object]:
        try:
            return reset_project(
                name,
                confirm_name=body.confirmName,
                dry_run=body.dryRun,
            )
        except Exception as err:
            raise _project_http_error(err) from err

    @app.post("/api/projects/{name}/delete")
    def projects_delete(name: str, body: ProjectConfirmBody) -> dict[str, object]:
        try:
            return delete_project(name, confirm_name=body.confirmName)
        except Exception as err:
            raise _project_http_error(err) from err

    @app.get("/api/projects/{name}/stats")
    def projects_stats(name: str) -> dict[str, object]:
        """What removing this project would take, for the confirmation to say."""
        return {
            "name": name,
            **project_contents(name, repo_root=resolve_repo_root()),
        }

    @app.get("/api/projects/{name}/export")
    def projects_export(name: str) -> FileResponse:
        """The whole project as one zip, removed from disk once it has been sent."""
        archive, filename, _count = export_project_archive(name)
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(archive.unlink, missing_ok=True),
        )

    @app.get("/api/trash")
    def trash_list() -> dict[str, object]:
        """What was removed and is still recoverable."""
        return {"items": list_trash(projects_dir(resolve_repo_root()))}

    @app.post("/api/trash/{item_id}/restore")
    def trash_restore(item_id: str) -> dict[str, object]:
        result = restore_trash(projects_dir(resolve_repo_root()), item_id)
        _settle_restored(result.get("restored"))
        return result

    @app.get("/api/projects/{name}/tree")
    def tree(name: str) -> dict[str, object]:
        layout = get_project_layout(name)
        return {"project": name, "files": list_project_tree(layout)}

    @app.get("/api/projects/{name}/source-citations")
    def source_citations(name: str) -> dict[str, object]:
        layout = get_project_layout(name)
        payload = source_citations_payload(layout)
        return {
            "project": name,
            "sources": payload["sources"],
            "collections": payload.get("collections") or [],
        }

    @app.get("/api/projects/{name}/revision-inbox")
    def revision_inbox(name: str) -> dict[str, object]:
        layout = require_project_layout(name)
        payload = revision_inbox_payload(layout)
        return {"project": name, **payload}

    @app.post("/api/projects/{name}/revision-inbox/ignore")
    def revision_inbox_ignore(
        name: str, body: RevisionInboxIgnoreBody
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            payload = ignore_inbox_item(layout, body.identity, body.kind)
        except KeyError as err:
            raise http_error(err, 404) from err
        return {"project": name, **payload}

    @app.post("/api/projects/{name}/revision-inbox/ack")
    def revision_inbox_ack(
        name: str, body: RevisionInboxAckBody
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            payload = acknowledge_deleted(layout, body.identity)
        except KeyError as err:
            raise http_error(err, 404) from err
        return {"project": name, **payload}

    @app.get("/api/projects/{name}/ingest-recovery")
    def ingest_recovery(name: str) -> dict[str, object]:
        layout = require_project_layout(name)
        payload = recovery_payload(layout)
        return {"project": name, **payload}

    @app.post("/api/projects/{name}/ingest-recovery/discard")
    def ingest_recovery_discard(
        name: str, body: IngestRecoveryDiscardBody
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            payload = discard_recovery_job(layout, body.id)
        except KeyError as err:
            raise http_error(err, 404) from err
        return {"project": name, **payload}

    @app.get("/api/projects/{name}/graph")
    def graph(name: str) -> dict[str, object]:
        try:
            layout = get_project_layout(name)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        payload = wiki_graph_payload(layout)
        return {"project": name, "nodes": payload["nodes"], "edges": payload["edges"]}

    @app.get("/api/projects/{name}/file")
    def file(
        name: str,
        path: str = Query(..., description="Path relative to project root"),
    ) -> dict[str, object]:
        try:
            layout = get_project_layout(name)
            return read_project_text(layout, path)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/file-bytes")
    def file_bytes(
        name: str,
        path: str = Query(..., description="Path relative to project root"),
    ) -> FileResponse:
        try:
            layout = get_project_layout(name)
            absolute, media_type = read_project_bytes(layout, path)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err
        return FileResponse(
            absolute,
            media_type=media_type,
            filename=absolute.name,
        )

    @app.post("/api/projects/{name}/file/delete")
    def project_file_delete(name: str, body: ProjectFileDeleteBody) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return delete_project_file(
                layout,
                body.path,
                project=name,
                trash_base=projects_dir(resolve_repo_root()),
            )
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/page")
    def project_page(name: str, path: str = Query(...)) -> dict[str, object]:
        """A wiki page as something to edit: prose, frontmatter, revision."""
        return page_view_payload(require_project_layout(name), path)

    @app.post("/api/projects/{name}/page/save")
    def project_page_save(name: str, body: PageSaveBody) -> dict[str, object]:
        try:
            return page_save_payload(require_project_layout(name), body.model_dump())
        except FileNotFoundError as err:
            raise http_error(err, 404) from err

    @app.post("/api/projects/{name}/page/rename")
    def project_page_rename(name: str, body: PageRenameBody) -> dict[str, object]:
        try:
            return page_rename_payload(require_project_layout(name), body.model_dump())
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/page/backlinks")
    def project_page_backlinks(name: str, path: str = Query(...)) -> dict[str, object]:
        """Who points at this page, for the confirmation that removes it."""
        return page_backlinks_payload(require_project_layout(name), path)

    @app.get("/api/projects/{name}/page/history")
    def project_page_history(name: str, path: str = Query(...)) -> dict[str, object]:
        return page_history_payload(require_project_layout(name), path)

    @app.get("/api/projects/{name}/page/version")
    def project_page_version(
        name: str,
        path: str = Query(...),
        id: str = Query(..., min_length=1, max_length=80),
    ) -> dict[str, object]:
        try:
            return page_version_payload(require_project_layout(name), path, id)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err

    @app.post("/api/projects/{name}/page/restore")
    def project_page_restore(name: str, body: PageRestoreBody) -> dict[str, object]:
        try:
            return page_restore_payload(require_project_layout(name), body.model_dump())
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/search")
    def project_search(
        name: str,
        q: str = Query("", max_length=200),
        limit: int = Query(8, ge=1, le=50),
    ) -> dict[str, object]:
        """Pages, remarks and sources matching a query, for the palette."""
        return project_search_payload(require_project_layout(name), q, per_kind=limit)

    @app.get("/api/projects/{name}/attention")
    def project_attention(name: str, check: bool = True) -> dict[str, object]:
        """Everything waiting on the reader, counted in one place."""
        return attention_payload(require_project_layout(name), check=check)

    @app.post("/api/projects/{name}/acquire/search")
    def project_acquire_search(name: str, body: AcquireSearchBody) -> dict[str, object]:
        require_project_layout(name)
        try:
            return run_search_payload(body.query, top_k=body.top_k, project=name)
        except ProviderNotConfiguredError as err:
            raise http_error(err, 503) from err
        except SearchConfigError as err:
            raise http_error(err, 400) from err
        except (SearchError, ValueError) as err:
            raise http_error(err, 502) from err

    @app.post("/api/projects/{name}/acquire/fetch")
    def project_acquire_fetch(name: str, body: AcquireFetchBody) -> dict[str, object]:
        require_project_layout(name)
        try:
            return run_fetch_payload(body.urls, query=body.query, project=name)
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/acquire/from-rendered")
    def project_acquire_from_rendered(
        name: str, body: AcquireRenderedBody
    ) -> dict[str, object]:
        require_project_layout(name)
        try:
            return run_rendered_payload(body.url, body.html, project=name)
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/acquire/from-local")
    def project_acquire_from_local(
        name: str, body: AcquireLocalBody
    ) -> dict[str, object]:
        require_project_layout(name)
        files = [
            {
                "name": item.name,
                "mediaType": item.mediaType,
                "contentBase64": item.contentBase64,
            }
            for item in body.files
        ]
        return run_local_payload(files, project=name)

    @app.post("/api/projects/{name}/acquire/save")
    def project_acquire_save(name: str, body: AcquireSaveBody) -> dict[str, object]:
        try:
            return run_save_payload(_captures_from_body(body), project=name)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/ingest")
    def project_ingest(name: str, body: IngestBody) -> StreamingResponse:
        layout = require_project_layout(name)
        try:
            resolve_http_ingest_paths(layout, body.paths)
            ensure_llm_configured()
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err
        except LlmConfigError as err:
            raise http_error(err, 503) from err
        return StreamingResponse(
            iter_ingest_sse(layout, body.paths, force=body.force),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/projects/{name}/deep-search")
    def project_deep_search(name: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return get_deep_search_payload(layout)
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.put("/api/projects/{name}/deep-search")
    def project_deep_search_put(name: str, body: DeepSearchPutBody) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return put_deep_search_payload(layout, body.model_dump())
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/deep-search/act")
    def project_deep_search_act(name: str, body: DeepSearchActBody) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return act_deep_search(layout, body.model_dump())
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/deep-search/act-stream")
    def project_deep_search_act_stream(
        name: str, body: DeepSearchActBody
    ) -> StreamingResponse:
        """The same action as above, said as it happens and able to be abandoned."""
        layout = require_project_layout(name)
        return StreamingResponse(
            iter_act_sse(layout, body.model_dump()),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/projects/{name}/clips")
    def project_clips_get(name: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return get_clips_payload(layout)
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.put("/api/projects/{name}/clips")
    def project_clips_put(name: str, body: ClipsPutBody) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return put_clips_payload(layout, body.model_dump())
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/brief")
    def project_brief_get(name: str) -> dict[str, object]:
        return brief_view(require_project_layout(name))

    @app.put("/api/projects/{name}/brief")
    def project_brief_put(name: str, body: BriefBody) -> dict[str, object]:
        return save_brief(require_project_layout(name), body.text)

    @app.get("/api/projects/{name}/chat-limits")
    def project_chat_limits_get(name: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return get_limits_payload(layout)
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.put("/api/projects/{name}/chat-limits")
    def project_chat_limits_put(name: str, body: ChatLimitsBody) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return put_limits_payload(layout, body.model_dump())
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/chats")
    def project_chats_list(name: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return list_chats_payload(layout)
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/chats")
    def project_chats_create(
        name: str, body: ChatCreateBody = Body(default_factory=ChatCreateBody)
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return post_conversation_payload(layout, body.model_dump())
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/chats/{conversation_id}")
    def project_chat_get(name: str, conversation_id: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return get_conversation_payload(layout, conversation_id)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.put("/api/projects/{name}/chats/{conversation_id}")
    def project_chat_put(
        name: str, conversation_id: str, body: ChatPutBody
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return put_conversation_payload(layout, conversation_id, body.model_dump())
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.patch("/api/projects/{name}/chats/{conversation_id}")
    def project_chat_rename(
        name: str, conversation_id: str, body: ChatRenameBody
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return rename_conversation_payload(layout, conversation_id, body.model_dump())
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.delete("/api/projects/{name}/chats/{conversation_id}")
    def project_chat_delete(name: str, conversation_id: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return delete_conversation_payload(layout, conversation_id)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/notes")
    def project_notes_list(
        name: str,
        page: str = Query("", description="Wiki page the notes are about"),
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return list_notes_payload(layout, page or None)
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.get("/api/projects/{name}/notes/{note_id}")
    def project_note_get(name: str, note_id: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return get_note_payload(layout, note_id)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/notes")
    def project_note_create(name: str, body: NoteCreateBody) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return create_note_payload(layout, body.model_dump())
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.put("/api/projects/{name}/notes/{note_id}")
    def project_note_put(
        name: str, note_id: str, body: NoteUpdateBody
    ) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return update_note_payload(layout, note_id, body.model_dump())
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.delete("/api/projects/{name}/notes/{note_id}")
    def project_note_delete(name: str, note_id: str) -> dict[str, object]:
        layout = require_project_layout(name)
        try:
            return delete_note_payload(layout, note_id)
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err

    @app.post("/api/projects/{name}/chats/{conversation_id}/query")
    def project_chat_query(
        name: str, conversation_id: str, body: ChatQueryBody
    ) -> StreamingResponse:
        layout = require_project_layout(name)
        try:
            ensure_llm_configured()
            get_conversation_payload(layout, conversation_id)
        except LlmConfigError as err:
            raise http_error(err, 503) from err
        except FileNotFoundError as err:
            raise http_error(err, 404) from err
        except ValueError as err:
            raise http_error(err, 400) from err
        return StreamingResponse(
            iter_chat_query_sse(layout, conversation_id, body.content),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/projects/{name}/topic-map")
    def project_topic_map(name: str, body: TopicMapBody) -> StreamingResponse:
        layout = require_project_layout(name)
        try:
            ensure_llm_configured()
        except LlmConfigError as err:
            raise http_error(err, 503) from err
        return StreamingResponse(
            iter_topic_map_sse(
                layout,
                intent=body.intent,
                source_identity=body.sourceIdentity,
                language=body.language,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/projects/{name}/ingest-confirmed")
    def project_ingest_confirmed(
        name: str, body: IngestConfirmedBody
    ) -> StreamingResponse:
        layout = require_project_layout(name)
        try:
            ensure_llm_configured()
        except LlmConfigError as err:
            raise http_error(err, 503) from err
        confirmed = [item.model_dump() for item in body.confirmed]
        return StreamingResponse(
            iter_ingest_confirmed_sse(layout, confirmed, intent=body.intent),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # A write names its project, so these two cannot answer: saying so beats
    # letting a caller believe a capture was stored somewhere.
    @app.post("/api/acquire/from-rendered")
    def acquire_rendered_unscoped() -> dict[str, str]:
        raise HTTPException(
            status_code=400,
            detail=(
                "Write target must be a project name. "
                "POST /api/projects/{name}/acquire/from-rendered"
            ),
        )

    @app.post("/api/acquire/save")
    def acquire_save_unscoped() -> dict[str, str]:
        raise HTTPException(
            status_code=400,
            detail="Write target must be a project name. POST /api/projects/{name}/acquire/save",
        )

    # Mounted after every route, because a mount at "/" answers everything the
    # routes above did not claim. Nothing here rewrites an unknown path to the
    # index: the interface has no client-side routes, so a missing file is a
    # missing file.
    web_dist = resolve_web_dist()
    if web_dist is None:
        @app.get("/", include_in_schema=False)
        def interface_not_built() -> PlainTextResponse:
            return PlainTextResponse(
                "The interface has not been built. Run `npm run build` in web/, "
                "then `node scripts/build-web.mjs`.\n",
                status_code=503,
            )
    else:
        app.mount("/", BuiltInterface(directory=web_dist, html=True), name="web")

    return app


app = create_app()
